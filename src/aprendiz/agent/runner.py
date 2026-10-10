"""AgentRunner: the Runner the supervisor calls. Owns connection, phases and cleanup."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aprendiz.agent.actor import ActorAdapter, ActorError, PixelJsonActor
from aprendiz.agent.budget import Budget, BudgetExceeded, GlobalSpend, SpendFileError
from aprendiz.agent.config import SCREEN_HEIGHT, SCREEN_WIDTH, AgentSettings
from aprendiz.agent.episode import EndTask, Episode, Fin
from aprendiz.agent.hoy import ParamError, needs_today, read_today, resolve_params
from aprendiz.agent.llm import Llm, LlmError, OpenRouterClient
from aprendiz.agent.planner import PlanError, plan_from_task
from aprendiz.agent.schemas import Plan
from aprendiz.agent.structured import InvalidReply
from aprendiz.agent.summary import TaskOutcome, build_summary
from aprendiz.agent.trace import TaskTrace
from aprendiz.agent.verifier import Captura, TipoVerificacion, verify
from aprendiz.config import Settings, VncTimings
from aprendiz.models import RunContext, RunResult
from aprendiz.vnc import VncClient, VncError

log = logging.getLogger(__name__)

_WRITE_TYPES = ("crear", "editar", "exportar")
_VERBS = {"create": "creado", "update": "actualizado", "delete": "eliminado", "export": "exportado"}


@dataclass
class Finish:
    estado: str
    fin: Fin
    motivo: str | None = None
    datos: list[str] = field(default_factory=list)
    respuesta: list[str] = field(default_factory=list)
    fuente: str | None = None
    hechos: list[str] = field(default_factory=list)
    verificado: bool = False

    @classmethod
    def failed(cls, fin: Fin, motivo: str, datos: list[str] | None = None) -> "Finish":
        return cls("fallida", fin, motivo, datos or [])


def facts_from_plan(plan: Plan, params: dict[str, str]) -> list[str]:
    """What a finished write task did, written from the plan, not by a model."""
    facts = []
    for write in plan.escrituras_permitidas:
        fields = ", ".join(f"{k}={params.get(v, v)}" for k, v in write.campos.items())
        facts.append(f"{write.entidad} {_VERBS[write.accion]}" + (f": {fields}" if fields else ""))
    return facts


class AgentRunner:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings | None = None,
        *,
        llm_factory: Callable[[Budget], Llm] | None = None,
        timings: VncTimings | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent or AgentSettings()
        self._timings = timings or VncTimings()
        self._llm_factory = llm_factory or self._default_llm
        self._global_spend: GlobalSpend | None = None

    def _default_llm(self, budget: Budget) -> Llm:
        return OpenRouterClient(self._settings, self._agent, budget)

    def _new_budget(self) -> Budget:
        if self._global_spend is None:
            self._global_spend = GlobalSpend(
                self._agent.GLOBAL_SPEND_FILE, self._settings.GLOBAL_BUDGET_USD
            )
        return Budget(self._settings.TASK_BUDGET_USD, self._global_spend)

    async def run(self, ctx: RunContext) -> RunResult:
        secrets = [
            ctx.escritorio.password,
            self._settings.OPENROUTER_API_KEY,
            self._settings.TEAM_TOKEN,
        ]
        trace = TaskTrace(
            self._settings.TRACES_DIR, ctx.task_id, secrets, self._agent.TRACE_SHOT_SIZE
        )
        client = VncClient(self._timings, lease=ctx.lease)
        ctx.register_client(client)
        llm: Llm | None = None
        finish = Finish.failed("error", "error interno del agente")
        try:
            llm = self._llm_factory(self._new_budget())
            finish = await self._run_task(ctx, client, llm, trace)
        except asyncio.CancelledError:
            finish = Finish.failed("error", "tarea cancelada")
            raise
        except EndTask as end:
            finish = Finish.failed(end.fin, end.motivo, end.datos)
        except BudgetExceeded:
            finish = Finish.failed("budget", "presupuesto agotado")
        except VncError as exc:
            finish = Finish.failed("error", f"se perdió el escritorio: {exc}")
        except (LlmError, PlanError, ActorError, InvalidReply, SpendFileError) as exc:
            log.error("task failed", extra={"task_id": ctx.task_id, "error": str(exc)})
            finish = Finish.failed("error", f"falló una dependencia del agente: {exc}")
        except Exception:
            log.exception("unexpected agent error", extra={"task_id": ctx.task_id})
        finally:
            try:
                await self._cleanup(client, llm)
            finally:
                trace.write_summary(finish.estado, finish.fin, finish.verificado)
        return self._result(finish, secrets)

    @staticmethod
    def _result(finish: Finish, secrets: list[str]) -> RunResult:
        outcome = TaskOutcome(
            estado="terminada" if finish.estado == "terminada" else "fallida",
            respuesta=finish.respuesta,
            fuente=finish.fuente,
            hechos=finish.hechos,
            motivo=finish.motivo,
            datos_motivo=finish.datos,
        )
        return RunResult(estado=outcome.estado, resumen=build_summary(outcome, secrets))

    async def _cleanup(self, client: VncClient, llm: Llm | None) -> None:
        """Release held input and close; each step bounded, none able to hang the task."""
        limit = self._agent.CLOSE_TIMEOUT_S
        for name, step in (("release", client.release_all), ("close", client.close)):
            try:
                await asyncio.wait_for(step(), limit)
            except Exception:
                log.warning("vnc %s failed during cleanup", name, exc_info=True)
        closer = getattr(llm, "aclose", None)
        if closer is not None:
            try:
                await asyncio.wait_for(closer(), limit)
            except Exception:
                log.warning("llm client close failed", exc_info=True)

    # ---- phases ---------------------------------------------------------------

    async def _run_task(
        self, ctx: RunContext, client: VncClient, llm: Llm, trace: TaskTrace
    ) -> Finish:
        await client.connect(ctx.escritorio.url, ctx.escritorio.password)
        first = await client.screenshot()
        if first.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
            return Finish.failed(
                "error",
                f"la pantalla del escritorio es de {first.width}x{first.height}; "
                f"se requiere {SCREEN_WIDTH}x{SCREEN_HEIGHT}",
            )
        plan = await plan_from_task(
            trace.llm(llm, "planner"),
            ctx.tarea,
            model=self._settings.PLANNER_MODEL,
            deadline=ctx.actor_deadline,
        )
        if plan.tipo == "peligrosa":
            return Finish.failed(
                "fail", "la tarea pide algo masivo o irreversible; no se ejecutó nada"
            )
        classifier = trace.llm(llm, "clasificador")
        params: dict[str, str] = {}
        hoy_text: str | None = None
        if plan.parametros:
            resolved = await self._resolve_params(ctx, plan, classifier, first)
            if isinstance(resolved, Finish):
                return resolved
            hoy_text, params = resolved
        actor: ActorAdapter = PixelJsonActor(trace.llm(llm, "actor"), self._settings, self._agent)
        episode = Episode(
            ctx=ctx,
            agent=self._agent,
            client=client,
            actor=actor,
            plan=plan,
            params=params,
            hoy_text=hoy_text,
            trace=trace,
            classifier_llm=classifier,
            auditor_llm=trace.llm(llm, "auditor"),
            detector_llm=trace.llm(llm, "detector"),
            table_llm=trace.llm(llm, "lector_tabla"),
        )
        try:
            last = await episode.run_execution()
        except EndTask as end:
            # A write already happened: never leave it unverified for lack of time.
            if (
                end.fin in ("actor_deadline", "error")
                and plan.tipo in _WRITE_TYPES
                and trace.writes_authorized > 0
            ):
                return await self._verify(
                    ctx, plan, params, episode, trace, trace.llm(llm, "verificador"), end.fin
                )
            raise
        if last.tipo == "fail":
            return Finish.failed(
                "fail", last.motivo or "el actor no pudo completar la tarea", last.datos_motivo
            )
        return await self._verify(
            ctx,
            plan,
            params,
            episode,
            trace,
            trace.llm(llm, "verificador"),
            claimed=last.respuesta,
        )

    async def _resolve_params(
        self, ctx: RunContext, plan: Plan, classifier: Llm, first
    ) -> tuple[str | None, dict[str, str]] | Finish:
        hoy = None
        if needs_today(plan.parametros):
            hoy = await self._read_hoy(ctx, classifier, first)
            if hoy is None:
                names = ", ".join(plan.parametros)
                return Finish.failed(
                    "error",
                    f"no se pudo leer la fecha 'Hoy' de la aplicación, necesaria para: {names}",
                )
        try:
            resolved = resolve_params(plan.parametros, hoy)
        except ParamError as exc:
            return Finish.failed("error", str(exc))
        fmt = self._agent.DATE_FORMAT
        hoy_text = hoy.strftime(fmt) if hoy else None
        return hoy_text, {name: value.strftime(fmt) for name, value in resolved.items()}

    async def _read_hoy(self, ctx: RunContext, classifier: Llm, first):
        return await read_today(
            classifier,
            first,
            model=self._agent.CLASSIFIER_MODEL,
            fallback=self._agent.CLASSIFIER_FALLBACK_MODEL,
            deadline=ctx.actor_deadline,
        )

    async def _verify(
        self,
        ctx: RunContext,
        plan: Plan,
        params: dict[str, str],
        episode: Episode,
        trace: TaskTrace,
        verifier_llm: Llm,
        fin: Fin = "done",
        claimed: list[str] | None = None,
    ) -> Finish:
        # After a timeout there is no navigation: verify with what is on screen now.
        navigate = fin == "done" and plan.tipo != "preguntar"
        if plan.tipo in _WRITE_TYPES and trace.writes_authorized == 0:
            return Finish.failed(
                fin, "el actor declaró terminar sin que se autorizara ninguna escritura"
            )
        if time.monotonic() >= ctx.deadline - self._agent.VERIFY_RESERVE_S:
            return Finish.failed(fin, "no quedó tiempo para verificar el resultado")
        finales = await episode.run_verification() if navigate else episode.static_evidence()
        evidencia = [Captura("results of the last search", s) for s in episode.search_shots[-2:]]
        evidencia += episode.question_views()
        if episode.persist_shot is not None:
            evidencia.append(Captura("the screen right after saving", episode.persist_shot))
        if episode.evidence_form is not None:
            evidencia.append(Captura("the form as it was before editing", episode.evidence_form))
        tipo: TipoVerificacion = plan.tipo  # type: ignore[assignment]  # "peligrosa" returned earlier
        result = await verify(
            verifier_llm,
            plan,
            tipo,
            evidencia,
            finales,
            model=self._agent.VERIFIER_MODEL,
            fallback=self._agent.VERIFIER_FALLBACK_MODEL,
            deadline=ctx.deadline - 2,
            memo=episode.memo,
            respuesta_actor=claimed,
            computed=episode.table_result is not None,
        )
        if not result.verificado:
            return Finish(
                "fallida",
                fin,
                "no se pudo verificar el resultado en pantalla",
                [*result.no_verificable, *result.datos_motivo],
            )
        return Finish(
            "terminada",
            fin,
            respuesta=result.respuesta or [],
            fuente=episode.memo
            if episode.table_result is not None
            else (result.observado[0] if plan.tipo == "preguntar" and result.observado else None),
            hechos=facts_from_plan(plan, params) if plan.tipo in _WRITE_TYPES else [],
            verificado=True,
        )

"""The step loop: capture, decide, classify, authorize, execute, detect, repeat.

Every input event the actor proposes goes through `_dispatch`; there is no other path to the
driver. Nothing here retries without awaiting a bounded operation, and CancelledError and
VncError are never caught: they unwind to the runner.
"""

import asyncio
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from aprendiz.agent.actor import ActorAdapter, ActorContext, ActorError
from aprendiz.agent.classifier import (
    Clasificacion,
    ClassificationCache,
    Modo,
    classify_event,
    crop_around,
    is_submit_button,
)
from aprendiz.agent.config import AgentSettings
from aprendiz.agent.detector import NOISE_PIXELS, changed_fraction, detect_effect
from aprendiz.agent.fields import read_field_value, value_matches
from aprendiz.agent.gate import EvidenceContext, GateState, Verdict, allowed_originals, authorize
from aprendiz.agent.llm import DeadlineExceeded, Llm, LlmError
from aprendiz.agent.memory import MemorySession
from aprendiz.agent.schemas import Action, Campo, Plan
from aprendiz.agent.textmatch import exact_hint
from aprendiz.agent.textrule import blank_region, crop_box, text_allowed
from aprendiz.agent.trace import TaskTrace
from aprendiz.agent.verifier import Captura
from aprendiz.models import RunContext
from aprendiz.vnc import VncClient

log = logging.getLogger(__name__)

Fin = Literal["done", "fail", "actor_deadline", "budget", "error"]
StepOutcome = Literal["continue", "done", "fail"]
_TEXT_FIELDS: set[str] = {"busqueda_filtro", "campo_registro"}
_HISTORY_LINE_CHARS = 240
_SEARCH_SHOTS_KEPT = 3
_WRITE_PLAN_TYPES = ("crear", "editar", "exportar")
_CONTINUITY_ACTIONS = {"create", "update"}


class EndTask(Exception):
    """The task ends now with this outcome."""

    def __init__(self, fin: Fin, motivo: str, datos: list[str] | None = None) -> None:
        super().__init__(motivo)
        self.fin: Fin = fin
        self.motivo = motivo
        self.datos = datos or []


@dataclass(frozen=True)
class FillRecord:
    """The last authorized fill_form: what it wrote and how the screen looked right after."""

    shot: Image.Image
    diff: str


class ActorGaveUp(Exception):
    """The actor produced two invalid replies in a row."""


class _FillAborted(Exception):
    """A form fill stopped part-way; the actor gets the reason and decides again."""


def action_key(action: Action) -> str:
    """Identity of an action for denial counting; nearby points count as the same click."""
    if action.tipo == "fill_form":
        return "fill:" + "|".join(f"{c.etiqueta}={c.valor}" for c in action.campos or [])
    cell = f"{(action.x or 0) // 30}:{(action.y or 0) // 30}"
    return f"{action.tipo}:{cell}:{action.combo}:{action.texto}"


def describe(action: Action) -> str:
    if action.tipo in ("click", "double_click", "hover"):
        return f"{action.tipo}({action.x},{action.y})"
    if action.tipo == "scroll":
        return f"scroll({action.x},{action.y},dy={action.dy})"
    if action.tipo == "type":
        return f"type {action.texto!r}"
    if action.tipo == "key":
        return f"key {action.combo}"
    if action.tipo == "fill_form":
        return "fill_form " + ", ".join(f"{c.etiqueta}={c.valor!r}" for c in action.campos or [])
    return action.tipo


class Episode:
    def __init__(
        self,
        *,
        ctx: RunContext,
        agent: AgentSettings,
        client: VncClient,
        actor: ActorAdapter,
        plan: Plan,
        params: dict[str, str],
        hoy_text: str | None,
        trace: TaskTrace,
        classifier_llm: Llm,
        auditor_llm: Llm,
        detector_llm: Llm,
        hints: tuple[str, ...] = (),
        notes: MemorySession | None = None,
    ) -> None:
        self.ctx = ctx
        self.agent = agent
        self.client = client
        self.actor = actor
        self.plan = plan
        self.params = params
        self.hoy_text = hoy_text
        self.trace = trace
        self._classifier_llm = classifier_llm
        self._auditor_llm = auditor_llm
        self._detector_llm = detector_llm
        self.hints = hints
        self._notes = notes or MemorySession(None, None)

        self.history: list[str] = []
        self.last_shot: Image.Image | None = None
        self.search_shots: list[Image.Image] = []
        self.evidence_form: Image.Image | None = None
        self._note: str | None = None
        self._gate_state = GateState()
        self._cache = ClassificationCache()
        self._focus: tuple[tuple[int, int], Clasificacion] | None = None
        self._form_initial: Image.Image | None = None
        self._prev_write: str | None = None
        self._unconfirmed: str | None = None
        self._denials: Counter[str] = Counter()
        self._invalid_replies = 0
        self._mode: Modo = "ejecucion"
        self._deadline = ctx.actor_deadline
        self._fill: FillRecord | None = None
        self._times: defaultdict[str, float] = defaultdict(float)
        self._last_effect_persisted = False
        self._signal: str | None = None
        self.persist_shot: Image.Image | None = None
        self._persisted_writes = 0
        self._plan_complete = False

    @contextmanager
    def _timed(self, label: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self._times[label] += time.monotonic() - started

    # ---- phases ----------------------------------------------------------

    async def run_execution(self) -> Action:
        """Returns the actor's terminal action (done or fail); any other end raises EndTask."""
        self._mode, self._deadline = "ejecucion", self.ctx.actor_deadline
        for _ in range(self.agent.MAX_STEPS):
            self._checkpoint()
            shot = await self._snap()
            terminal = await self._step(shot)
            if terminal is not None:
                return terminal
            if self._plan_complete:
                return Action(tipo="done", razon="persistence signal completed the plan's writes")
        raise EndTask("actor_deadline", "se alcanzó el tope de pasos sin terminar la tarea")

    async def run_verification(self) -> list[Captura]:
        """Read-only navigation to put the evidence on screen. Returns the screenshots seen."""
        self._mode = "verificacion"
        self._deadline = self.ctx.deadline - self.agent.VERIFY_RESERVE_S
        self._note = self._verification_note()
        shots: list[Captura] = []
        for n in range(1, self.agent.VERIFY_MAX_STEPS + 1):
            if self._time_left() < self.agent.MIN_CALL_S:
                break
            self.ctx.lease.ensure_active()
            shot = await self._snap()
            shots.append(Captura(f"verification step {n}", shot))
            try:
                terminal = await self._step(shot)
            except ActorGaveUp:
                break
            if terminal is not None:
                break
        if time.monotonic() < self.ctx.deadline - 2:
            shots.append(Captura("final screen", await self._snap()))
        return shots[-self.agent.VERIFIER_MAX_EVIDENCE_SHOTS :]

    def _verification_note(self) -> str | None:
        """Points the read-only navigation at the target values of the plan's writes."""
        targets = [
            f"{field}={self.params.get(value, value)}"
            for write in self.plan.escrituras_permitidas
            for field, value in write.campos.items()
        ]
        if not targets:
            return None
        seen = f"The save showed: {self._signal!r}. " if self._signal else ""
        return (
            f"{seen}Make these target values visible on screen (reopen the record if they are "
            f"not; read only): {', '.join(targets)}"
        )

    # ---- one step --------------------------------------------------------

    def _time_left(self) -> float:
        return self._deadline - time.monotonic()

    def static_evidence(self) -> list[Captura]:
        """What is on screen now, with no navigation: for a task that ran out of time."""
        return [Captura("final screen", self.last_shot)] if self.last_shot is not None else []

    def _checkpoint(self) -> None:
        self.ctx.lease.ensure_active()
        if self._time_left() < self.agent.MIN_CALL_S:
            raise EndTask("actor_deadline", "se agotó el tiempo disponible para actuar")

    async def _snap(self) -> Image.Image:
        started = time.monotonic()
        # screenshot() already waits for the framebuffer to settle; the copy itself is free, so
        # the whole call is the stability wait. (Waiting separately would double it.)
        with self._timed("espera_estabilidad"):
            shot = await self.client.screenshot()
        self.trace.stability_waits.append(round(time.monotonic() - started, 3))
        self.last_shot = shot
        return shot

    async def _step(self, shot: Image.Image) -> Action | None:
        """Runs one actor decision. Returns the action if it is terminal (done or fail)."""
        self.trace.steps += 1
        rec: dict = {"paso": self.trace.steps, "fase": self._mode}
        step_started = time.monotonic()
        self._times = defaultdict(float)
        try:
            action = await self._decide(shot)
            if action is None:
                rec["nota"] = "invalid actor reply"
                return None
            rec["accion"] = action.model_dump(exclude_none=True, exclude={"razon"})
            outcome = await self._process(action, shot, rec)
            return action if outcome in ("done", "fail") else None
        finally:
            rec["nota_al_actor"] = self._note
            rec["tiempos_s"] = {
                **{k: round(v, 2) for k, v in sorted(self._times.items())},
                "total": round(time.monotonic() - step_started, 2),
            }
            self.trace.record_step(shot, rec)

    async def _decide(self, shot: Image.Image) -> Action | None:
        contexto = self._context(self._note)
        self._note = None
        try:
            with self._timed("actor"):
                action = await self.actor.next_action(shot, contexto)
        except ActorError as exc:
            self._invalid_replies += 1
            log.warning("actor reply invalid", extra={"error": str(exc)})
            if self._invalid_replies >= 2:
                if self._mode == "verificacion":
                    raise ActorGaveUp from exc
                raise EndTask("error", "el actor no devolvió una acción válida") from exc
            self._note = "Your last reply was not a valid action. Reply with one valid JSON action."
            return None
        except DeadlineExceeded as exc:
            if self._mode == "verificacion":
                raise ActorGaveUp from exc
            raise EndTask("actor_deadline", "se agotó el tiempo esperando al modelo") from exc
        except LlmError:
            if time.monotonic() >= self._deadline:
                raise EndTask("actor_deadline", "se agotó el tiempo esperando al modelo") from None
            raise
        self._invalid_replies = 0
        return action

    def _context(self, note: str | None) -> ActorContext:
        return ActorContext(
            tarea=self.ctx.tarea,
            plan=self.plan,
            history=self.history,
            hoy_app=self.hoy_text,
            seconds_left=max(0.5, self._deadline - time.monotonic()),
            note=note,
            parametros=self.params,
            modo=self._mode,
            pistas=self.hints,
        )

    def _log(self, line: str) -> None:
        self.history.append(f"{self.trace.steps}. {line}"[:_HISTORY_LINE_CHARS])

    async def _process(self, action: Action, shot: Image.Image, rec: dict) -> StepOutcome:
        kind = action.tipo
        if kind in ("done", "fail"):
            return kind
        if kind == "wait":
            await asyncio.sleep(
                min(self.agent.WAIT_ACTION_S, max(0.0, self._deadline - time.monotonic()))
            )
            self._log("wait")
            return "continue"
        if kind == "fill_form":
            await self._fill_form(action, shot, rec)
            return "continue"
        if kind in ("hover", "scroll"):
            await self._dispatch(action, shot, "navegacion", (action.x or 0, action.y or 0), rec)
        elif kind in ("click", "double_click"):
            point = (action.x or 0, action.y or 0)
            clase, saves = await self._classify_click(shot, point, kind)
            rec["clasificacion"] = clase
            await self._dispatch(
                action, shot, clase, point, rec, by_continuity=saves and clase == "compromiso"
            )
        elif kind == "key":
            await self._key(action, shot, rec)
        elif kind == "type":
            await self._type(action, shot, rec)
        return "continue"

    # ---- classification and authorization ----------------------------------

    def _continuity_open(self, shot: Image.Image) -> bool:
        """A fill_form was authorized and the form is, within noise, as that fill left it."""
        if self._fill is None or self._mode != "ejecucion":
            return False
        if not {w.accion for w in self.plan.escrituras_permitidas} <= _CONTINUITY_ACTIONS:
            return False
        changed = changed_fraction(self._fill.shot, shot)[0]
        return changed <= self.agent.CONTINUITY_MAX_PIXELS

    async def _classify_click(
        self, shot: Image.Image, point: tuple[int, int], tipo_evento: str
    ) -> tuple[Clasificacion, bool]:
        """Classification, plus (in parallel, only while a fill is waiting to be saved) whether
        the point is the form's save button."""
        if not self._continuity_open(shot):
            return await self._classify(shot, point, tipo_evento), False

        async def save_check() -> bool:
            with self._timed("boton_guardar"):
                return await is_submit_button(
                    self._classifier_llm,
                    shot,
                    point,
                    model=self.agent.CLASSIFIER_MODEL,
                    fallback=self.agent.CLASSIFIER_FALLBACK_MODEL,
                    deadline=self._deadline,
                )

        outcomes = await asyncio.gather(
            self._classify(shot, point, tipo_evento), save_check(), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        clase, saves = outcomes
        return clase, bool(saves)

    async def _classify(self, shot: Image.Image, point: tuple[int, int], tipo_evento: str):
        with self._timed("clasificador"):
            return await self._classify_call(shot, point, tipo_evento)

    async def _classify_call(self, shot: Image.Image, point: tuple[int, int], tipo_evento: str):
        return await classify_event(
            self._classifier_llm,
            shot,
            point,
            tipo_evento,
            self._mode,
            model=self.agent.CLASSIFIER_MODEL,
            fallback=self.agent.CLASSIFIER_FALLBACK_MODEL,
            votes=self.agent.CLASSIFIER_VOTES,
            cache=self._cache,
            deadline=self._deadline,
        )

    def _deny(self, action: Action, motivo: str, datos: list[str], rec: dict, final: bool) -> None:
        """Bounce the action back to the actor, or end the task if it keeps coming back."""
        key = action_key(action)
        self._denials[key] += 1
        self._log(f"{describe(action)} -> denied: {motivo}")
        self._note = f"Denied: {motivo}"
        too_many = self._denials[key] > self.agent.MAX_CORRECTABLE_DENIALS
        if self._mode == "ejecucion" and (final or too_many):
            raise EndTask("fail", motivo, datos)

    async def _authorize(
        self, action: Action, shot: Image.Image, point: tuple[int, int], rec: dict
    ) -> Verdict | None:
        key = action_key(action)
        if self._unconfirmed == key:
            verdict = Verdict(
                decision="denegar_corregible",
                motivo="the previous attempt showed no visible change; search for the entity "
                "again to see whether it took effect before repeating",
            )
        else:
            evidence = EvidenceContext(
                captura_actual=shot,
                capturas_busqueda=list(self.search_shots),
                captura_formulario_inicial=None
                if action.tipo == "fill_form"
                else self._form_initial,
                recorte_evento=crop_around(shot, point),
                parametros=self.params,
                continuacion_de=self._prev_write,
            )
            with self._timed("auditor"):
                verdict = await authorize(
                    self._auditor_llm,
                    self.plan,
                    action,
                    evidence,
                    model=self.agent.AUDITOR_MODEL,
                    fallback=self.agent.AUDITOR_FALLBACK_MODEL,
                    state=self._gate_state,
                    deadline=self._deadline,
                )
        rec["veredicto"] = verdict.model_dump()
        if verdict.decision == "permitir":
            self.trace.writes_authorized += 1
            return verdict
        self.trace.writes_denied += 1
        self._deny(action, verdict.motivo, verdict.datos, rec, verdict.decision == "denegar_final")
        return None

    # ---- events -------------------------------------------------------------

    async def _key(self, action: Action, shot: Image.Image, rec: dict) -> None:
        combo = action.combo or ""
        if combo.lower().replace(" ", "").split("+")[-1] == "tab":
            self._deny(
                action, "never use Tab to move between fields; click each field", [], rec, False
            )
            return
        if self._focus is None:
            clase: Clasificacion = "compromiso"  # no focus point to look at: deny by default
            point = (0, 0)
        else:
            point = self._focus[0]
            clase = await self._classify(shot, point, f"key:{combo.lower()}")
        rec["clasificacion"] = clase
        await self._dispatch(action, shot, clase, point, rec)

    async def _type(self, action: Action, shot: Image.Image, rec: dict) -> None:
        if self._focus is None or self._focus[1] not in _TEXT_FIELDS:
            self._deny(action, "click a text field before typing", [], rec, False)
            return
        if not text_allowed(self.plan, self.ctx.tarea, self.params, action.texto or ""):
            known = [*allowed_originals(self.plan, self.params), *self.params.values()]
            hint = exact_hint(action.texto or "", known)
            self._deny(
                action,
                f"that text is not from the task, the plan or the code{hint}",
                [],
                rec,
                False,
            )
            return
        point, clase = self._focus
        rec["clasificacion"] = clase
        await self._dispatch(action, shot, clase, point, rec)

    async def _dispatch(
        self,
        action: Action,
        shot: Image.Image,
        clase: Clasificacion,
        point: tuple[int, int],
        rec: dict,
        by_continuity: bool = False,
    ) -> None:
        edits_record = clase == "campo_registro" and action.tipo in ("type", "key")
        needs_gate = clase == "compromiso" or edits_record
        verdict: Verdict | None = None
        if needs_gate:
            if self._mode == "verificacion":
                self._deny(
                    action, "verification is read-only: this event could write", [], rec, False
                )
                return
            if by_continuity and self._fill is not None:
                verdict = self._authorize_by_continuity(rec)
            else:
                self._fill = None  # any other gated event ends the pending save window
                verdict = await self._authorize(action, shot, point, rec)
                if verdict is None:
                    return
            if edits_record and self._form_initial is None:
                self._form_initial = shot
                self.evidence_form = shot
        self._prev_write = None  # an event is about to run: the pending confirmation window closes
        if action.tipo == "type":
            await self._type_into(point, action.texto or "")
        else:
            await self._emit(action)
        after, changed = await self._after(action, shot, clase, verdict is not None, rec)
        self._record_outcome(action, clase, point, verdict, shot, after, changed)
        if verdict is not None and clase == "compromiso" and self._last_effect_persisted:
            self._count_persisted_write(rec)

    def _authorize_by_continuity(self, rec: dict) -> Verdict:
        """The save of a form whose fill_form the auditor already authorized, unchanged since.
        It is the same write, so no new model call; the trace says so."""
        assert self._fill is not None
        verdict = Verdict(
            decision="permitir",
            motivo=f"autorizado por continuidad: {self._fill.diff}",
        )
        rec["veredicto"] = {**verdict.model_dump(), "autorizado_por": "continuidad"}
        self._fill = None
        return verdict

    def _count_persisted_write(self, rec: dict) -> None:
        self._persisted_writes += 1
        needed = len(self.plan.escrituras_permitidas)
        if self.plan.tipo in _WRITE_PLAN_TYPES and self._persisted_writes >= needed > 0:
            self._plan_complete = True
            rec["plan_completo"] = True

    async def _emit(self, action: Action) -> None:
        with self._timed("ejecucion"):
            await self._emit_event(action)

    async def _emit_event(self, action: Action) -> None:
        if action.tipo in ("click", "double_click"):
            await self.client.click(
                action.x or 0, action.y or 0, double=action.tipo == "double_click"
            )
        elif action.tipo == "hover":
            await self.client.move(action.x or 0, action.y or 0)
        elif action.tipo == "scroll":
            await self.client.scroll(action.x or 0, action.y or 0, action.dy or 0)
        elif action.tipo == "key":
            await self.client.key(action.combo or "")

    async def _after(
        self, action: Action, before: Image.Image, clase: Clasificacion, authorized: bool, rec: dict
    ) -> tuple[Image.Image, bool]:
        after = await self._snap()
        with self._timed("detector"):
            effect = await detect_effect(
                self._detector_llm,
                before,
                after,
                describe(action),
                clase,
                authorized,
                model=self.agent.CLASSIFIER_MODEL,
                fallback=self.agent.CLASSIFIER_FALLBACK_MODEL,
                deadline=self._deadline,
            )
        rec["efecto"] = effect.model_dump()
        self._last_effect_persisted = effect.persistio
        if effect.persistio and authorized:
            self._signal = effect.señal or self._signal
            self.persist_shot = after
        if effect.debe_abortar:
            raise EndTask(
                "fail",
                "se detectó una escritura no autorizada tras la acción " + describe(action),
                [effect.señal] if effect.señal else [],
            )
        return after, changed_fraction(before, after)[0] > NOISE_PIXELS

    def _record_outcome(
        self,
        action: Action,
        clase: Clasificacion,
        point: tuple[int, int],
        verdict: Verdict | None,
        shot: Image.Image,
        after: Image.Image,
        changed: bool,
    ) -> None:
        self._log(
            f"{describe(action)} [{clase}] -> "
            + ("screen changed" if changed else "no visible change")
        )
        if action.tipo in ("click", "double_click"):
            self._focus = (point, clase)
        if not changed and action.tipo in ("click", "double_click", "key"):
            self._note = (
                f"No visible change after {describe(action)}. Do not assume it worked: "
                "re-read the screen before deciding."
            )
        if clase == "navegacion" and changed:
            # A new view or form: a baseline taken on the previous screen (e.g. the list where
            # the search box was typed) would make the auditor compare against the wrong form.
            self._form_initial = None
            self.evidence_form = None
        if clase == "busqueda_filtro" and changed:
            self._unconfirmed = None
            self.search_shots = [*self.search_shots, after][-_SEARCH_SHOTS_KEPT:]
        if verdict is not None and clase == "compromiso":
            self._form_initial = None
            self._prev_write = verdict.motivo or describe(action)
            if not changed:
                self._unconfirmed = action_key(action)
        if self._mode != "ejecucion":
            return
        if action.texto:
            self._notes.remember_business(action.texto)
        self._notes.note_action(action.tipo)
        if clase == "navegacion" and action.tipo in ("click", "double_click"):
            self._notes.note_navigation(action.razon)

    # ---- typing -----------------------------------------------------------------

    async def _write_field(self, text: str) -> Image.Image:
        with self._timed("ejecucion"):
            await self.client.key("ctrl+a")  # callers confirmed this point is a text field
            await self.client.type_text(text)
        return await self._snap()

    async def _read_value(self, shot: Image.Image, point: tuple[int, int]) -> str | None:
        with self._timed("lectura_campo"):
            return await read_field_value(
                self._classifier_llm,
                shot,
                point,
                model=self.agent.CLASSIFIER_MODEL,
                fallback=self.agent.CLASSIFIER_FALLBACK_MODEL,
                deadline=self._deadline,
            )

    def _note_mismatch(self, point: tuple[int, int], read: str | None, text: str) -> None:
        self._note = (
            f"The field at {point} reads {read!r} after typing {text!r}. Check it before saving."
        )

    async def _type_into(self, point: tuple[int, int], text: str) -> None:
        """Replace the field content and re-read it; rewrite once if it does not match."""
        read = await self._read_value(await self._write_field(text), point)
        if value_matches(read, text):
            return
        log.warning(
            "typed value does not match, rewriting once",
            extra={"read": read, "expected": text},
        )
        read = await self._read_value(await self._write_field(text), point)
        if not value_matches(read, text):
            self._note_mismatch(point, read, text)

    # ---- batch form fill -----------------------------------------------------------

    async def _fill_form(self, action: Action, shot: Image.Image, rec: dict) -> None:
        campos = action.campos or []
        if self._mode == "verificacion":
            self._deny(action, "verification is read-only: this event could write", [], rec, False)
            return
        first = (campos[0].x, campos[0].y)
        rec["clasificacion"] = "campo_registro"
        self._fill = None
        verdict = await self._authorize(action, shot, first, rec)
        if verdict is None:
            return
        self._notes.note_action("fill_form")
        for campo in campos:
            self._notes.remember_business(campo.valor)
        self._prev_write = None
        if self._form_initial is None:
            self._form_initial = shot
            self.evidence_form = shot
        abort: str | None = None
        try:
            await self._fill_fields(campos, shot, rec)
        except _FillAborted as exc:
            abort = str(exc)
        after, changed = await self._after(action, shot, "campo_registro", True, rec)
        self._log(
            f"{describe(action)} [campo_registro] -> "
            + (
                f"stopped: {abort}"
                if abort
                else "screen changed"
                if changed
                else "no visible change"
            )
        )
        if abort:
            self._note = f"The form fill stopped: {abort}. Look at the screen and decide again."
        else:
            self._fill = FillRecord(after, describe(action))

    async def _fill_fields(self, campos: list[Campo], first_shot: Image.Image, rec: dict) -> None:
        """One direct click per field. The re-read of a text field overlaps with the recapture
        and classification of the next one; a mismatch is settled before the next click."""
        pending: tuple[asyncio.Task[str | None], tuple[int, int], str] | None = None
        last: tuple[Image.Image, tuple[int, int]] | None = None
        try:
            for campo in campos:
                shot = first_shot if last is None else await self._snap()
                point = (campo.x, campo.y)
                if last is not None and self._layout_shifted(last[0], shot, last[1]):
                    point = await self._relocate(shot, campo)
                clase = await self._classify(shot, point, "click")
                await self._settle_read(pending)
                pending = None
                if campo.tipo == "select":
                    last = (await self._fill_select(point, clase, campo), point)
                    continue
                if clase in _TEXT_FIELDS:
                    with self._timed("ejecucion"):
                        await self.client.click(*point)
                else:
                    clase = await self._focus_by_click(point, shot, campo, rec)
                self._focus = (point, clase)
                typed = await self._write_field(campo.valor)
                pending = (
                    asyncio.create_task(self._read_value(typed, point)),
                    point,
                    campo.valor,
                )
                last = (typed, point)
            await self._settle_read(pending)
            pending = None
        finally:
            if pending is not None:
                pending[0].cancel()
                await asyncio.gather(pending[0], return_exceptions=True)

    async def _settle_read(
        self, pending: tuple[asyncio.Task[str | None], tuple[int, int], str] | None
    ) -> None:
        if pending is None:
            return
        task, point, text = pending
        read = await task
        if value_matches(read, text):
            return
        log.warning(
            "typed value does not match, rewriting once",
            extra={"read": read, "expected": text},
        )
        with self._timed("ejecucion"):
            await self.client.click(*point)
        read = await self._read_value(await self._write_field(text), point)
        if not value_matches(read, text):
            self._note_mismatch(point, read, text)

    async def _fill_select(self, point: tuple[int, int], clase: Clasificacion, campo: Campo):
        """Open the dropdown, recapture, and click the option whose text is the value. The
        actor only locates the option; the value to choose is the plan's. Covered by the
        fill_form's authorization."""
        if clase == "compromiso":
            raise _FillAborted(
                f"the point for {campo.etiqueta!r} looks like a button, not a select"
            )
        with self._timed("ejecucion"):
            await self.client.click(*point)
        opened = await self._snap()
        option = await self._locate(
            opened,
            f"The dropdown of {campo.etiqueta!r} is open. Reply with a click action on the "
            f"option whose text is exactly {campo.valor!r}; the code clicks it.",
            f"the option {campo.valor!r} of {campo.etiqueta!r}",
        )
        with self._timed("ejecucion"):
            await self.client.click(*option)
        self._focus = (point, "campo_registro")
        return await self._snap()

    async def _focus_by_click(
        self, point: tuple[int, int], shot: Image.Image, campo: Campo, rec: dict
    ) -> Clasificacion:
        """The point of an already authorized fill was classified as a commitment. A click on an
        input only gives it focus, and a focused input shows a border or caret, so classify the
        crop again after clicking. The click itself is not authorized: the effect detector
        judges it, and aborts the task if the point turned out to be a button that persisted."""
        click = Action(tipo="click", x=point[0], y=point[1])
        await self.client.click(*point)
        after, _ = await self._after(click, shot, "compromiso", False, rec)
        clase = await self._classify(after, point, "type")
        if clase not in _TEXT_FIELDS:
            raise _FillAborted(f"the point for {campo.etiqueta!r} is not a text field")
        return clase

    @staticmethod
    def _layout_shifted(
        previous: Image.Image, current: Image.Image, previous_point: tuple[int, int]
    ) -> bool:
        """True if anything changed outside the field we just filled (validation, reflow)."""
        box = crop_box(previous_point, previous.size, 400, 40)
        return (
            changed_fraction(blank_region(previous, box), blank_region(current, box))[0]
            > NOISE_PIXELS
        )

    async def _relocate(self, shot: Image.Image, campo: Campo) -> tuple[int, int]:
        return await self._locate(
            shot,
            f"The form layout changed. Reply with a click action on the field labelled "
            f"{campo.etiqueta!r}; the code types its value.",
            repr(campo.etiqueta),
        )

    async def _locate(self, shot: Image.Image, instruction: str, what: str) -> tuple[int, int]:
        """Ask the actor for the coordinates of a control; it must answer with a click."""
        try:
            with self._timed("actor"):
                action = await self.actor.next_action(shot, self._context(instruction))
        except (ActorError, DeadlineExceeded) as exc:
            raise _FillAborted(f"could not locate {what}") from exc
        if action.tipo != "click":
            raise _FillAborted(f"could not locate {what}")
        return (action.x or 0, action.y or 0)

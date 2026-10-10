"""Evaluation harness: reset -> task -> check against a desktop, scored by eval.scoring.

Usage: uv run --env-file .env python -m eval.harness --pruebas DIR [--pruebas DIR2]
       [--solo E1,V3] [--repeticiones N] [--etiqueta texto] [--api URL] [--dry-run]
       [--sin-vigilancia]
Secrets and URLs come from the environment (see HarnessEnv), never from arguments.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from aprendiz.redact import redact
from eval.desktop_lock import DesktopBusy, DesktopLock
from eval.report import Resultado, RunReport, render
from eval.schema import LoadedTest, load_dir
from eval.scoring import categoria, evaluate, verdict
from eval.vigilancia import InitialScreenWatch, vnc_capture

log = logging.getLogger("eval.harness")

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
LOCK_WAIT_S = 90.0
RESULTS_DIR = Path(__file__).parent / "resultados"


class MissingEnv(Exception):
    pass


class StepError(Exception):
    """A harness step failed; the run is recorded as failed with this message."""


@dataclass(frozen=True)
class HarnessEnv:
    desktop_url: str
    desktop_password: str
    desktop_token: str
    team_token: str
    openrouter_key: str
    traces_dir: str
    budget_usd: float

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "HarnessEnv":
        required = ["DESKTOP_URL", "DESKTOP_PASSWORD", "DESKTOP_TOKEN", "TEAM_TOKEN"]
        missing = [name for name in required if not environ.get(name)]
        if missing:
            raise MissingEnv(f"faltan variables de entorno: {', '.join(missing)}")
        return cls(
            desktop_url=environ["DESKTOP_URL"].rstrip("/"),
            desktop_password=environ["DESKTOP_PASSWORD"],
            desktop_token=environ["DESKTOP_TOKEN"],
            team_token=environ["TEAM_TOKEN"],
            openrouter_key=environ.get("OPENROUTER_API_KEY", ""),
            traces_dir=environ.get("TRACES_DIR", "traces"),
            budget_usd=float(environ.get("HARNESS_BUDGET_USD", "30")),
        )

    @property
    def secrets(self) -> list[str]:
        return [self.desktop_password, self.desktop_token, self.team_token, self.openrouter_key]

    @property
    def websocket_url(self) -> str:
        scheme, _, rest = self.desktop_url.partition("://")
        mapped = {"https": "wss", "http": "ws"}.get(scheme)
        if mapped is None:
            raise MissingEnv(f"DESKTOP_URL debe empezar con http:// o https://: {scheme}://")
        return f"{mapped}://{rest}/websockify"


@dataclass(frozen=True)
class Timings:
    poll_interval_s: float = 5.0
    task_cap_s: float = 300.0
    reset_settle_s: float = 2.0
    health_timeout_s: float = 60.0
    health_interval_s: float = 1.0
    post_limit_s: float = 2.0


def read_cost(traces_dir: str, task_id: str) -> tuple[float | None, list[dict[str, Any]]]:
    """Cost and call latencies from the agent's trace summary; (None, []) if absent."""
    path = Path(traces_dir) / task_id / "resumen.json"
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cost = float(data["costo_usd"])
        calls = [
            {"rol": c.get("rol"), "latencia_s": c.get("latencia_s")}
            for c in data.get("llamadas", [])
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("resumen.json ilegible para %s: %s", task_id, exc)
        return None, []
    return cost, calls


class Harness:
    def __init__(
        self,
        env: HarnessEnv,
        desktop: httpx.AsyncClient,
        api: httpx.AsyncClient,
        openrouter: httpx.AsyncClient | None,
        timings: Timings | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        watch: InitialScreenWatch | None = None,
    ) -> None:
        self._env, self._desktop, self._api, self._or = env, desktop, api, openrouter
        self._t, self._sleep, self._clock = timings or Timings(), sleep, clock
        self._watch = watch

    # ---- desktop ---------------------------------------------------------

    async def reset_desktop(self) -> None:
        try:
            response = await self._desktop.post("/__reset")
        except httpx.HTTPError as exc:
            raise StepError(f"/__reset falló: {type(exc).__name__}") from exc
        if response.status_code >= 300:
            raise StepError(f"/__reset respondió {response.status_code}")
        await self._wait_health()
        await self._sleep(self._t.reset_settle_s)

    async def _wait_health(self) -> None:
        start = self._clock()
        while True:
            try:
                response = await self._desktop.get("/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if self._clock() - start >= self._t.health_timeout_s:
                raise StepError("el escritorio no respondió /health tras el reset")
            await self._sleep(self._t.health_interval_s)

    async def check(self, raw: Any) -> dict[str, Any]:
        try:
            response = await self._desktop.post("/__check", json=raw)
        except httpx.HTTPError as exc:
            raise StepError(f"/__check falló: {type(exc).__name__}") from exc
        if response.status_code == 400:
            raise _TestError(f"/__check rechazó la prueba (400): {response.text[:200]}")
        if response.status_code != 200:
            raise StepError(f"/__check respondió {response.status_code}")
        return response.json()

    # ---- our API ---------------------------------------------------------

    async def _post_task(self, instruccion: str) -> tuple[str, float]:
        body = {
            "tarea": instruccion,
            "escritorio": {"url": self._env.websocket_url, "password": self._env.desktop_password},
        }
        start = self._clock()
        try:
            response = await self._api.post("/tasks", json=body)
        except httpx.HTTPError as exc:
            raise StepError(f"POST /tasks falló: {type(exc).__name__}") from exc
        latency = self._clock() - start
        if response.status_code != 200:
            raise StepError(f"POST /tasks respondió {response.status_code}")
        return response.json()["task_id"], latency

    async def _poll(self, task_id: str) -> dict[str, Any] | None:
        start = self._clock()
        while self._clock() - start < self._t.task_cap_s:
            await self._sleep(self._t.poll_interval_s)
            try:
                response = await self._api.get(f"/tasks/{task_id}")
                data = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                data = None
            if data and data.get("estado") != "en_curso":
                return data
        return None

    async def budget_usage(self) -> float | None:
        if self._or is None:
            return None
        try:
            response = await self._or.get(OPENROUTER_KEY_URL)
            return float(response.json()["data"]["usage"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("no se pudo leer el gasto de la key: %s", type(exc).__name__)
            return None

    # ---- one run ---------------------------------------------------------

    async def run_one(self, test: LoadedTest, repeticion: int) -> Resultado:
        prueba = test.prueba
        assert prueba is not None
        r = Resultado(test.test_id, test.source, categoria(prueba), repeticion)
        try:
            await self.reset_desktop()
            if self._watch is not None:
                r.avisos += await self._watch.check(test.test_id)
            r.task_id, r.post_latencia_s = await self._post_task(prueba.instruccion)
            r.post_lento = r.post_latencia_s >= self._t.post_limit_s
            started = self._clock()
            final = await self._poll(r.task_id)
            r.duracion_s = self._clock() - started
            if final is None:
                r.sin_respuesta = True
            else:
                r.estado, r.resumen = final.get("estado"), final.get("resumen", "")
            r.check = await self.check(test.raw)
        except _TestError as exc:
            r.error_prueba = str(exc)
            return r
        except StepError as exc:
            r.motivos.append(str(exc))
        self._score(r, test)
        return r

    def _score(self, r: Resultado, test: LoadedTest) -> None:
        assert test.prueba is not None
        rules = evaluate(test.prueba, r.estado, r.resumen, self._env.secrets)
        v = verdict(r.check, rules, r.estado)
        r.reglas = [asdict(rule) for rule in rules]
        r.exito, r.falso_exito, r.destructiva = v.exito, v.falso_exito, v.destructiva
        if r.task_id:
            r.costo_usd, r.llamadas = read_cost(self._env.traces_dir, r.task_id)
        if r.sin_respuesta:
            r.motivos.append(f"sin respuesta en {self._t.task_cap_s:.0f} s")
        r.motivos += [f"regla {x.nombre}: {x.detalle}" for x in rules if not x.ok and x.detalle]
        if r.check is not None:
            failed = [c.get("nombre") for c in r.check.get("checks", []) if c.get("ok") is False]
            r.motivos += [f"check '{name}' falló" for name in failed]
            if not r.check.get("pass") and not failed:
                r.motivos.append("check pass=false")
        if r.post_lento:
            r.motivos.append(f"POST /tasks lento ({r.post_latencia_s:.1f} s)")

    # ---- whole run -------------------------------------------------------

    async def run_all(self, tests: list[LoadedTest], repeticiones: int) -> RunReport:
        report = RunReport(repeticiones=repeticiones)
        if self._or is None:
            report.avisos.append("sin OPENROUTER_API_KEY: no se controla el presupuesto")
        attempted = False
        try:
            for test in tests:
                if test.prueba is None:
                    report.resultados.append(
                        Resultado(test.test_id, test.source, "-", 0, error_prueba=test.error)
                    )
                    continue
                for rep in range(1, repeticiones + 1):
                    usage = await self.budget_usage()
                    if usage is not None and usage >= self._env.budget_usd:
                        report.presupuesto_cortado = usage
                        return report
                    attempted = True
                    report.resultados.append(await self.run_one(test, rep))
        finally:
            if self._watch is not None:
                report.avisos.extend(self._watch.notices)
            if attempted:
                await self._final_reset(report)
        return report

    async def _final_reset(self, report: RunReport) -> None:
        try:
            await self._desktop.post("/__reset")
        except httpx.HTTPError as exc:
            report.avisos.append(f"el reset final falló: {type(exc).__name__}")


class _TestError(Exception):
    """The test itself is invalid (e.g. the judge returned 400)."""


# ---- output ----------------------------------------------------------------


def redact_tree(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_tree(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: redact_tree(v, secrets) for k, v in value.items()}
    return value


def write_results(
    report: RunReport, etiqueta: str, secrets: list[str], directory: Path, now: datetime
) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", etiqueta).strip("-") or "sin-etiqueta"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:%Y%m%d-%H%M%S}-{safe}.json"
    payload = {
        "fecha": now.isoformat(timespec="seconds"),
        "etiqueta": etiqueta,
        "repeticiones": report.repeticiones,
        "presupuesto_cortado": report.presupuesto_cortado,
        "avisos": report.avisos,
        "resultados": [asdict(r) for r in report.resultados],
    }
    clean = redact_tree(payload, secrets)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def select_tests(dirs: list[str], solo: str | None) -> list[LoadedTest]:
    tests = [t for d in dirs for t in load_dir(Path(d).expanduser())]
    if solo:
        wanted = {s.strip() for s in solo.split(",") if s.strip()}
        unknown = wanted - {t.test_id for t in tests}
        if unknown:
            print(f"AVISO: --solo no encontró: {', '.join(sorted(unknown))}", file=sys.stderr)
        tests = [t for t in tests if t.test_id in wanted]
    return tests


def dry_run(tests: list[LoadedTest]) -> int:
    invalid = 0
    for t in tests:
        if t.prueba is None:
            invalid += 1
            print(f"ERROR  {t.test_id}  {Path(t.source).name}: {t.error}")
        else:
            p = t.prueba
            print(
                f"ok     {p.id:<6} {categoria(p):<12} {p.estado_esperado:<10} {Path(t.source).name}"
            )
    print(f"\n{len(tests)} pruebas, {invalid} con error de esquema")
    return 1 if invalid else 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eval.harness", description=__doc__)
    parser.add_argument("--pruebas", action="append", required=True, metavar="DIR")
    parser.add_argument("--solo", help="ids separados por coma, p. ej. E1,V3")
    parser.add_argument("--repeticiones", type=int, default=1)
    parser.add_argument("--etiqueta", default="")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--sin-vigilancia",
        action="store_true",
        help="no comparar la pantalla inicial con la referencia antes de cada prueba",
    )
    args = parser.parse_args(argv)
    if args.repeticiones < 1:
        parser.error("--repeticiones debe ser >= 1")
    return args


async def run(args: argparse.Namespace, tests: list[LoadedTest], env: HarnessEnv) -> int:
    auth = lambda token: {"Authorization": f"Bearer {token}"}  # noqa: E731
    async with (
        httpx.AsyncClient(
            base_url=env.desktop_url, headers=auth(env.desktop_token), timeout=30
        ) as desktop,
        httpx.AsyncClient(base_url=args.api, headers=auth(env.team_token), timeout=30) as api,
        httpx.AsyncClient(
            headers=auth(env.openrouter_key) if env.openrouter_key else {}, timeout=15
        ) as router,
    ):
        watch = (
            None
            if args.sin_vigilancia
            else InitialScreenWatch(vnc_capture(env.websocket_url, env.desktop_password))
        )
        harness = Harness(env, desktop, api, router if env.openrouter_key else None, watch=watch)
        report = await harness.run_all(tests, args.repeticiones)
    print(redact(render(report), env.secrets))
    path = write_results(report, args.etiqueta, env.secrets, RESULTS_DIR, datetime.now())
    print(f"\nResultados: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    tests = select_tests(args.pruebas, args.solo)
    if args.dry_run:
        return dry_run(tests)
    try:
        env = HarnessEnv.from_environ(os.environ)
        env.websocket_url  # noqa: B018 - fail early on a malformed DESKTOP_URL
    except MissingEnv as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    lock = DesktopLock.from_env()
    try:
        # The incident monitor may be mid-round; wait for it instead of colliding.
        lock.acquire(LOCK_WAIT_S)
    except DesktopBusy as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args, tests, env))
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())

"""Incident monitor: detects UI changes on the desktop without any LLM.

Usage: uv run python -m eval.incidente referencia
       uv run python -m eval.incidente vigilar [--intervalo 60]
Environment: DESKTOP_URL (base http(s)), DESKTOP_PASSWORD, optional DESKTOP_LOCK.
Only navigation clicks are ever sent (menus and list views, never action buttons).
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image

from aprendiz.vnc import VncClient
from eval.desktop_lock import DesktopLock
from eval.evidencia import Tracker, append_log, boxes_json, save_evidence
from eval.imgdiff import Thresholds, dhash

log = logging.getLogger("eval.incidente")

BASE_DIR = Path(__file__).parent / "incidente"
PARK = (1279, 799)
ARCHIVO, INICIO = (42, 48), (110, 76)
COMERCIAL, MAESTROS, CLIENTES = (122, 48), (120, 76), (326, 76)
SCREENS = ["inicio", "menu_comercial", "clientes"]

Capture = dict[str, Image.Image]


@dataclass
class RoundResult:
    captures: Capture
    health_code: int | None
    health_latency_s: float | None


async def _click(client: VncClient, point: tuple[int, int]) -> None:
    await client.click(*point)
    await client.wait_stable()


async def _go_home(client: VncClient) -> None:
    await _click(client, ARCHIVO)
    await _click(client, INICIO)


async def capture_screens(client: VncClient) -> Capture:
    """Navigation-only tour. Ends on Inicio so the app is not left elsewhere."""
    captures: Capture = {}
    await client.move(*PARK)
    await _go_home(client)
    await client.move(*PARK)
    captures["inicio"] = await client.screenshot()

    await _click(client, COMERCIAL)
    await client.move(*PARK)
    captures["menu_comercial"] = await client.screenshot()

    # Escape does not close this menu and a second click on Comercial toggles it
    # shut, so the tour goes on from the open menu.
    for point in (MAESTROS, CLIENTES):
        await _click(client, point)
    await client.move(*PARK)
    captures["clientes"] = await client.screenshot()

    await _go_home(client)
    return captures


async def check_health(base_url: str) -> tuple[int | None, float | None]:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(f"{base_url}/health")
        return response.status_code, time.monotonic() - start
    except httpx.HTTPError:
        return None, time.monotonic() - start


def websocket_url(base: str) -> str:
    scheme, _, rest = base.partition("://")
    mapped = {"https": "wss", "http": "ws"}.get(scheme)
    if mapped is None:
        raise ValueError("DESKTOP_URL debe empezar con http:// o https://")
    return f"{mapped}://{rest.rstrip('/')}/websockify"


async def do_round(base_url: str, password: str) -> RoundResult:
    code, latency = await check_health(base_url)
    async with VncClient() as client:
        await client.connect(websocket_url(base_url), password)
        captures = await capture_screens(client)
    return RoundResult(captures, code, latency)


# ---- change tracking ---------------------------------------------------------


# ---- persistence ----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now().astimezone()


def save_reference(base: Path, captures: Capture, when: datetime) -> Path:
    folder = base / "referencia"
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, image in captures.items():
        image.save(folder / f"{name}.png")
        hashes[name] = f"{dhash(image):016x}"
    meta = {"hora": when.isoformat(timespec="seconds"), "dhash": hashes}
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return folder


def load_references(base: Path) -> Capture:
    folder = base / "referencia"
    missing = [n for n in SCREENS if not (folder / f"{n}.png").is_file()]
    if missing:
        raise FileNotFoundError(f"falta la referencia ({', '.join(missing)}): corre 'referencia'")
    return {n: Image.open(folder / f"{n}.png").convert("RGB") for n in SCREENS}


def process_round(tracker: Tracker, result: RoundResult, base: Path, when: datetime) -> list[str]:
    """Log the round and return the console lines for anything worth an alert."""
    stamp = f"{when:%H:%M:%S}"
    lines: list[str] = []
    if tracker.health_changed(result.health_code):
        lines.append(f"{stamp} ALERTA /health respondió {result.health_code}")
    append_log(
        base,
        {
            "hora": when.isoformat(timespec="seconds"),
            "tipo": "ronda",
            "health": result.health_code,
            "health_latencia_s": round(result.health_latency_s or 0, 3),
        },
    )
    for screen in SCREENS:
        comparison, alert = tracker.observe(screen, result.captures[screen])
        entry: dict[str, object] = {
            "hora": when.isoformat(timespec="seconds"),
            "pantalla": screen,
            "distancia": comparison.distance,
            "area": comparison.area,
            "cajas": boxes_json(comparison.boxes),
            "cambio": comparison.changed,
        }
        if alert is not None:
            folder = save_evidence(base, alert, when)
            entry["evidencia"] = str(folder)
            lines.append(
                f"{stamp} CAMBIO en {screen}: distancia {comparison.distance}, "
                f"área {comparison.area}px, {len(comparison.boxes)} cajas -> {folder}"
            )
        append_log(base, entry)
    return lines


# ---- commands ---------------------------------------------------------------------


def _env() -> tuple[str, str]:
    url, password = os.environ.get("DESKTOP_URL"), os.environ.get("DESKTOP_PASSWORD")
    if not url or not password:
        raise SystemExit("DESKTOP_URL y DESKTOP_PASSWORD deben estar definidas")
    return url.rstrip("/"), password


async def cmd_referencia(base: Path, lock: DesktopLock) -> int:
    url, password = _env()
    await asyncio.to_thread(lock.acquire, 90)
    try:
        result = await do_round(url, password)
    finally:
        lock.release()
    folder = save_reference(base, result.captures, _now())
    print(f"Referencia guardada en {folder} (/health {result.health_code})")
    return 0


async def cmd_vigilar(
    base: Path,
    lock: DesktopLock,
    interval_s: float,
    thresholds: Thresholds,
    rounds: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    round_fn: Callable[[str, str], Awaitable[RoundResult]] = do_round,
) -> int:
    url, password = _env()
    tracker = Tracker(load_references(base), thresholds)
    done = 0
    while rounds is None or done < rounds:
        done += 1
        started = time.monotonic()
        when = _now()
        await _watch_once(base, lock, tracker, url, password, when, round_fn)
        if rounds is None or done < rounds:
            await sleep(max(0.0, interval_s - (time.monotonic() - started)))
    return 0


async def _watch_once(
    base: Path,
    lock: DesktopLock,
    tracker: Tracker,
    url: str,
    password: str,
    when: datetime,
    round_fn: Callable[[str, str], Awaitable[RoundResult]],
) -> None:
    stamp = when.isoformat(timespec="seconds")
    if not lock.try_acquire():
        append_log(base, {"hora": stamp, "tipo": "ocupado"})
        print(f"{when:%H:%M:%S} ocupado: otro proceso usa el escritorio, ronda saltada")
        return
    try:
        result = await round_fn(url, password)
    except Exception as exc:  # the monitor must survive any failed round
        log.exception("round failed")
        append_log(base, {"hora": stamp, "tipo": "error", "error": f"{type(exc).__name__}: {exc}"})
        print(f"{when:%H:%M:%S} ERROR de conexión o navegación: {type(exc).__name__}")
        code, _ = await check_health(url)
        if tracker.health_changed(code):
            print(f"{when:%H:%M:%S} ALERTA /health respondió {code}")
        return
    finally:
        lock.release()
    lines = process_round(tracker, result, base, when)
    for line in lines:
        print(line)
    if not lines:
        print(f"{when:%H:%M:%S} sin cambios")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eval.incidente", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("referencia")
    watch = sub.add_parser("vigilar")
    watch.add_argument("--intervalo", type=float, default=60.0)
    defaults = Thresholds()
    watch.add_argument("--umbral-hash", type=int, default=defaults.hash_distance)
    watch.add_argument("--umbral-area", type=int, default=defaults.changed_area_px)
    watch.add_argument("--caja-minima", type=int, default=defaults.min_box_px)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Line buffering so alerts reach a redirected log file the moment they happen.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    lock = DesktopLock.from_env()
    if args.cmd == "referencia":
        return asyncio.run(cmd_referencia(BASE_DIR, lock))
    thresholds = Thresholds(
        hash_distance=args.umbral_hash,
        changed_area_px=args.umbral_area,
        min_box_px=args.caja_minima,
    )
    try:
        return asyncio.run(cmd_vigilar(BASE_DIR, lock, args.intervalo, thresholds))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

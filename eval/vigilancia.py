"""Initial-screen watch for the harness: capture-only comparison against the monitor's reference."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from aprendiz.vnc import VncClient
from eval.evidencia import Tracker, append_log, boxes_json, save_evidence
from eval.imgdiff import Thresholds
from eval.incidente import BASE_DIR, PARK

log = logging.getLogger("eval.vigilancia")

SCREEN = "inicio"
SCREEN_ALERT = "inicio-harness"
CHANGED_WARNING = "pantalla inicial distinta de la referencia"
CAPTURE_TIMEOUT_S = 30.0

Capture = Callable[[], Awaitable[Image.Image]]


def vnc_capture(websocket_url: str, password: str) -> Capture:
    """Capture only: park the mouse, take a screenshot, no clicks."""

    async def capture() -> Image.Image:
        async with VncClient() as client:
            await client.connect(websocket_url, password)
            await client.move(*PARK)
            return await client.screenshot()

    return capture


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class InitialScreenWatch:
    capture: Capture
    base: Path = BASE_DIR
    thresholds: Thresholds = field(default_factory=Thresholds)
    now: Callable[[], datetime] = _now
    notices: list[str] = field(default_factory=list)  # run-level warnings, reported once
    # Host of the desktop under test; a reference taken on another desktop is ignored.
    desktop_host: str | None = None
    _tracker: Tracker | None = None
    _reference_checked: bool = False

    def _load_tracker(self) -> Tracker | None:
        if not self._reference_checked:
            self._reference_checked = True
            path = self.base / "referencia" / f"{SCREEN}.png"
            if path.is_file() and not self._same_desktop():
                self.notices.append(
                    "la referencia es de otro escritorio: no se vigila la pantalla inicial"
                )
            elif path.is_file():
                self._tracker = Tracker({SCREEN: Image.open(path).convert("RGB")}, self.thresholds)
            else:
                self.notices.append(
                    "sin referencia de pantalla inicial (corre 'eval.incidente referencia'): "
                    "no se vigila la pantalla inicial"
                )
        return self._tracker

    def _same_desktop(self) -> bool:
        meta_path = self.base / "referencia" / "meta.json"
        if self.desktop_host is None or not meta_path.is_file():
            return True
        reference_host = json.loads(meta_path.read_text(encoding="utf-8")).get("host")
        return reference_host is None or reference_host == self.desktop_host

    async def check(self, test_id: str) -> list[str]:
        """Warnings for this test. Never raises: a failed capture must not break the run."""
        tracker = self._load_tracker()
        if tracker is None:
            return []
        when = self.now()
        stamp = when.isoformat(timespec="seconds")
        try:
            image = await asyncio.wait_for(self.capture(), CAPTURE_TIMEOUT_S)
            comparison, alert = tracker.observe(SCREEN, image)
        except Exception as exc:
            log.warning("initial-screen capture failed: %s", type(exc).__name__)
            error = f"{type(exc).__name__}: {exc}"
            append_log(
                self.base,
                {
                    "hora": stamp,
                    "origen": "harness",
                    "prueba": test_id,
                    "tipo": "error",
                    "error": error,
                },
            )
            return [f"captura de vigilancia falló ({type(exc).__name__})"]
        entry: dict[str, object] = {
            "hora": stamp,
            "origen": "harness",
            "prueba": test_id,
            "pantalla": SCREEN,
            "distancia": comparison.distance,
            "area": comparison.area,
            "cajas": boxes_json(comparison.boxes),
            "cambio": comparison.changed,
        }
        warnings: list[str] = []
        if alert is not None:
            folder = save_evidence(self.base, replace(alert, screen=SCREEN_ALERT), when)
            entry["evidencia"] = str(folder)
            warnings.append(f"{CHANGED_WARNING} (evidencia: {folder})")
        append_log(self.base, entry)
        return warnings

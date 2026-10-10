import json
from datetime import datetime
from pathlib import Path

import pytest
from eval.desktop_lock import DesktopLock
from eval.imgdiff import Thresholds
from eval.incidente import (
    SCREENS,
    RoundResult,
    Tracker,
    cmd_vigilar,
    process_round,
)
from PIL import Image, ImageDraw

T = Thresholds()


def page(*rects: tuple[int, int, int, int], color: int = 0) -> Image.Image:
    """A 320x200 synthetic screen: light grey with a dark header and optional rectangles."""
    img = Image.new("RGB", (320, 200), (230, 230, 230))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 319, 30), fill=(40, 60, 120))
    for r in rects:
        draw.rectangle(r, fill=(color, color, color))
    return img


def test_alert_not_repeated_until_the_view_changes_again() -> None:
    ref = page()
    tracker = Tracker({"inicio": ref})
    changed, other = page((100, 80, 219, 159)), page((20, 60, 300, 190), color=90)

    assert tracker.observe("inicio", ref)[1] is None
    assert tracker.observe("inicio", changed)[1] is not None  # first sight
    assert tracker.observe("inicio", changed)[1] is None  # same changed view: silent
    assert tracker.observe("inicio", other)[1] is not None  # changed again
    assert tracker.observe("inicio", ref)[1] is None  # back to normal
    assert tracker.observe("inicio", changed)[1] is not None  # a new incident alerts again


def test_health_alerts_only_on_transition() -> None:
    tracker = Tracker({})
    assert [tracker.health_changed(c) for c in (200, 503, 503, None, 200, 500)] == [
        False,
        True,
        False,
        False,
        False,
        True,
    ]


def make_round(tracker_images: dict[str, Image.Image], health: int = 200) -> RoundResult:
    return RoundResult(tracker_images, health, 0.05)


def test_process_round_writes_evidence_and_full_timeline(tmp_path: Path) -> None:
    refs = {name: page() for name in SCREENS}
    tracker = Tracker(refs)
    when = datetime(2026, 10, 14, 15, 31, 5).astimezone()

    quiet = process_round(tracker, make_round(dict(refs)), tmp_path, when)
    assert quiet == []

    broken = {**refs, "clientes": page((100, 80, 219, 159))}
    lines = process_round(tracker, make_round(broken, health=503), tmp_path, when)
    assert any("/health respondió 503" in line for line in lines)
    assert any("CAMBIO en clientes" in line for line in lines)

    folder = tmp_path / "20261014-153105-clientes"
    assert {p.name for p in folder.iterdir()} == {"antes.png", "despues.png", "diff.png"}
    entries = [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()]
    screens = [e for e in entries if e.get("pantalla") == "clientes"]
    assert [e["cambio"] for e in screens] == [False, True]
    assert screens[1]["cajas"] and screens[1]["area"] > 0
    assert sum(e.get("tipo") == "ronda" for e in entries) == 2  # every round is logged


# ---- lock -----------------------------------------------------------------------


async def test_busy_and_failed_rounds_are_logged_and_the_loop_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DESKTOP_URL", "http://desk.invalid")
    monkeypatch.setenv("DESKTOP_PASSWORD", "pw")
    base = tmp_path / "inc"
    (base / "referencia").mkdir(parents=True)
    for name in SCREENS:
        page().save(base / "referencia" / f"{name}.png")

    async def failing(url: str, password: str) -> RoundResult:
        raise ConnectionError("down")

    async def no_sleep(_: float) -> None:
        return None

    async def fast_health(_: str):  # noqa: ANN202
        return None, 0.0

    monkeypatch.setattr("eval.incidente.check_health", fast_health)
    lock_path = tmp_path / "d.lock"
    other = DesktopLock(lock_path)
    assert other.try_acquire()
    await cmd_vigilar(base, DesktopLock(lock_path), 0, Thresholds(), 1, no_sleep, failing)
    other.release()
    await cmd_vigilar(base, DesktopLock(lock_path), 0, Thresholds(), 1, no_sleep, failing)

    kinds = [json.loads(x)["tipo"] for x in (base / "log.jsonl").read_text().splitlines()]
    assert kinds == ["ocupado", "error"]

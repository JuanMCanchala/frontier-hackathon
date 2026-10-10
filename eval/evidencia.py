"""Change tracking and evidence writing shared by the incident monitor and the harness."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from eval.imgdiff import Box, Comparison, Thresholds, compare, draw_boxes


@dataclass
class Alert:
    screen: str
    comparison: Comparison
    reference: Image.Image
    current: Image.Image


@dataclass
class Tracker:
    """Compares captures to the reference and alerts only when the view changes again."""

    references: dict[str, Image.Image]
    thresholds: Thresholds = field(default_factory=Thresholds)
    _last_alerted: dict[str, Image.Image] = field(default_factory=dict)
    _last_health_bad: bool = False

    def observe(self, screen: str, image: Image.Image) -> tuple[Comparison, Alert | None]:
        result = compare(self.references[screen], image, self.thresholds)
        if not result.changed:
            self._last_alerted.pop(screen, None)
            return result, None
        previous = self._last_alerted.get(screen)
        if previous is not None and not compare(previous, image, self.thresholds).changed:
            return result, None
        self._last_alerted[screen] = image
        return result, Alert(screen, result, self.references[screen], image)

    def health_changed(self, code: int | None) -> bool:
        """True only on the transition from healthy to bad, so a long outage alerts once."""
        bad = code != 200
        fire = bad and not self._last_health_bad
        self._last_health_bad = bad
        return fire


def append_log(base: Path, entry: dict[str, object]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    with (base / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_evidence(base: Path, alert: Alert, when: datetime) -> Path:
    folder = base / f"{when:%Y%m%d-%H%M%S}-{alert.screen}"
    folder.mkdir(parents=True, exist_ok=True)
    alert.reference.save(folder / "antes.png")
    alert.current.save(folder / "despues.png")
    draw_boxes(alert.current, alert.comparison.boxes).save(folder / "diff.png")
    return folder


def boxes_json(boxes: list[Box]) -> list[list[int]]:
    return [list(b) for b in boxes]

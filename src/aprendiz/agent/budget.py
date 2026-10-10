"""Cost accounting: per-task budget with up-front reservation and a persistent global counter."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """A call was refused because its worst-case cost does not fit a spending cap."""


class SpendFileError(Exception):
    """The persistent spend counter exists but cannot be trusted."""


class GlobalSpend:
    """Spend counter shared by all tasks and kept across restarts.

    A corrupt file is an error, not a reset: silently restarting from zero would defeat the cap.
    """

    def __init__(self, path: str | Path, limit_usd: float) -> None:
        self._path = Path(path)
        self._limit = limit_usd
        self._reserved = 0.0
        self._spent = self._load()

    @property
    def spent(self) -> float:
        return self._spent

    def _load(self) -> float:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0.0
        try:
            return float(json.loads(raw)["spent_usd"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SpendFileError(f"unreadable spend file {self._path}") from exc

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".spend-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"spent_usd": self._spent}, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def reserve(self, amount: float) -> None:
        if self._spent + self._reserved + amount > self._limit:
            raise BudgetExceeded(
                f"global budget exhausted (spent {self._spent:.4f} of {self._limit:.2f} USD)"
            )
        self._reserved += amount

    def settle(self, reserved: float, actual: float) -> None:
        self._reserved = max(0.0, self._reserved - reserved)
        self._spent += actual
        self._persist()

    def release(self, reserved: float) -> None:
        self._reserved = max(0.0, self._reserved - reserved)


@dataclass(frozen=True)
class Reservation:
    amount: float


class Budget:
    """Per-task cap. `reserve` is called before every model call with its worst-case cost."""

    def __init__(self, limit_usd: float, global_spend: GlobalSpend | None = None) -> None:
        self._limit = limit_usd
        self._global = global_spend
        self._spent = 0.0
        self._reserved = 0.0

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def reserved(self) -> float:
        return self._reserved

    @property
    def remaining(self) -> float:
        return self._limit - self._spent - self._reserved

    def reserve(self, max_cost: float) -> Reservation:
        if max_cost > self.remaining:
            raise BudgetExceeded(
                f"task budget exhausted (spent {self._spent:.4f}, call may cost up to "
                f"{max_cost:.4f}, cap {self._limit:.2f} USD)"
            )
        if self._global is not None:
            self._global.reserve(max_cost)
        self._reserved += max_cost
        return Reservation(max_cost)

    def settle(self, reservation: Reservation, actual_cost: float) -> None:
        self._reserved = max(0.0, self._reserved - reservation.amount)
        self._spent += actual_cost
        if self._global is not None:
            self._global.settle(reservation.amount, actual_cost)

    def release(self, reservation: Reservation) -> None:
        """Give back a reservation for a call that produced no billable response."""
        self._reserved = max(0.0, self._reserved - reservation.amount)
        if self._global is not None:
            self._global.release(reservation.amount)

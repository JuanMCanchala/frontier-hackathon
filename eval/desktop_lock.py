"""Cross-process lock so only one script drives the desktop at a time.

    with DesktopLock.from_env().hold(timeout_s=90):
        ...  # use the desktop

The lock is an flock on a file (DESKTOP_LOCK, default data/desktop.lock); the OS
releases it if the holder dies.
"""

import fcntl
import os
import time
from pathlib import Path
from types import TracebackType
from typing import IO

DEFAULT_PATH = "data/desktop.lock"


class DesktopBusy(Exception):
    pass


class DesktopLock:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._file: IO[bytes] | None = None
        self._timeout_s = 0.0

    @classmethod
    def from_env(cls) -> "DesktopLock":
        return cls(os.environ.get("DESKTOP_LOCK") or DEFAULT_PATH)

    @property
    def held(self) -> bool:
        return self._file is not None

    def try_acquire(self) -> bool:
        """Take the lock without blocking. Returns False if someone else holds it."""
        if self._file is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("ab")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._file = handle
        return True

    def acquire(self, timeout_s: float, poll_s: float = 0.25) -> None:
        """Wait up to timeout_s for the lock; raise DesktopBusy if it never frees."""
        end = time.monotonic() + timeout_s
        while not self.try_acquire():
            if time.monotonic() >= end:
                raise DesktopBusy(f"el escritorio sigue ocupado tras {timeout_s:.0f} s")
            time.sleep(poll_s)

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def hold(self, timeout_s: float = 90.0) -> "DesktopLock":
        """Configure the wait used by `with lock.hold(...)`."""
        self._timeout_s = timeout_s
        return self

    def __enter__(self) -> "DesktopLock":
        self.acquire(self._timeout_s)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

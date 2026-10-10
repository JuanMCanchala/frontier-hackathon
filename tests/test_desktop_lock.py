from pathlib import Path

import pytest
from eval.desktop_lock import DesktopBusy, DesktopLock


def test_second_nonblocking_acquire_fails_while_held(tmp_path: Path) -> None:
    path = tmp_path / "d.lock"
    first, second = DesktopLock(path), DesktopLock(path)
    assert first.try_acquire()
    assert not second.try_acquire()
    first.release()
    assert second.try_acquire()
    second.release()


def test_acquire_times_out_and_context_manager_releases(tmp_path: Path) -> None:
    path = tmp_path / "d.lock"
    holder = DesktopLock(path)
    with holder.hold(timeout_s=1):
        with pytest.raises(DesktopBusy):
            DesktopLock(path).acquire(0.3, poll_s=0.05)
    assert DesktopLock(path).try_acquire()


# ---- watch loop -------------------------------------------------------------------

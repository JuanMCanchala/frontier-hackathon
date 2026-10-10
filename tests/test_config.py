import pytest
from pydantic import ValidationError

from aprendiz.config import Settings, VncTimings


def test_secrets_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_deadlines_leave_time_to_verify_and_close() -> None:
    settings = Settings(OPENROUTER_API_KEY="k", TEAM_TOKEN="t", _env_file=None)  # type: ignore[call-arg]
    assert settings.ACTOR_STOP_S < settings.TASK_DEADLINE_S < 300


def test_click_is_held_long_enough_for_the_desktop() -> None:
    # The remote desktop ignores clicks shorter than 100 ms.
    assert VncTimings().click_hold_s >= 0.1

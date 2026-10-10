"""Single place for configuration: environment settings and driver timings."""

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings read from the environment. Secrets have no default."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENROUTER_API_KEY: str
    TEAM_TOKEN: str
    ACTOR_MODEL: str = "anthropic/claude-haiku-5.5"
    PLANNER_MODEL: str = "anthropic/claude-sonnet-5.5"
    ACTOR_STOP_S: float = 220
    TASK_DEADLINE_S: float = 260
    TASK_BUDGET_USD: float = 0.15
    GLOBAL_BUDGET_USD: float = 30
    TRACES_DIR: str = "traces"
    TASKS_DB: str = "data/tasks.sqlite3"
    APP_MEMORY_DB: str = "data/app_memory.sqlite3"
    STOP_GRACE_S: float = 2.0
    PREVIOUS_RELEASE_S: float = 3.0
    HOST: str = "0.0.0.0"
    PORT: int = 8000


@dataclass(frozen=True)
class VncTimings:
    """Timings of the VNC driver, in seconds.

    The remote desktop ignores clicks shorter than 100 ms and keeps repainting
    for up to 1 s after an action, hence the hold and quiet-window values.
    """

    click_hold_s: float = 0.15
    double_click_gap_s: float = 0.08
    key_delay_s: float = 0.02
    non_ascii_delay_s: float = 0.06
    stable_quiet_s: float = 0.5
    stable_timeout_s: float = 3.0
    connect_timeout_s: float = 15.0
    force_close_timeout_s: float = 1.0

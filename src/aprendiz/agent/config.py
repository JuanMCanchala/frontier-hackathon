"""Agent configuration: models, limits and the price table used to reserve cost.

Task and global spend caps (TASK_BUDGET_USD, GLOBAL_BUDGET_USD) live in
aprendiz.config.Settings and are not repeated here.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 800

# OpenRouter list prices, (input, output) USD per million tokens. Used only to bound a call
# BEFORE it is made; the real cost always comes from usage.cost.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "anthropic/claude-sonnet-5.5": (2.0, 10.0),
    "openai/gpt-6.1-sol": (2.0, 10.0),
    "openai/gpt-6-luna": (0.10, 0.50),
}
# Unknown models are priced like the most expensive known one, so a typo never under-reserves.
DEFAULT_PRICE_USD_PER_MTOK: tuple[float, float] = (2.0, 10.0)
# Safety margin over the price table for the up-front reservation (token counts are approximate).
RESERVE_MARGIN = 1.2


class AgentSettings(BaseSettings):
    """ACTOR_MODEL and PLANNER_MODEL are read from aprendiz.config.Settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    VERIFIER_MODEL: str = "anthropic/claude-sonnet-5.5"
    AUDITOR_MODEL: str = "openai/gpt-6.1-sol"
    CLASSIFIER_MODEL: str = "anthropic/claude-haiku-5.5"

    # Used once when the provider's moderation blocks a request to the primary model.
    CLASSIFIER_FALLBACK_MODEL: str = "openai/gpt-6-luna"
    AUDITOR_FALLBACK_MODEL: str = "anthropic/claude-sonnet-5.5"
    ACTOR_FALLBACK_MODEL: str = "anthropic/claude-sonnet-5.5"  # the other of haiku/sonnet
    VERIFIER_FALLBACK_MODEL: str = "anthropic/claude-haiku-5.5"
    CLASSIFIER_VOTES: int = 2

    MAX_STEPS: int = 45
    LLM_TIMEOUT_S: float = 30
    LLM_RETRIES: int = 2
    LLM_BACKOFF_S: float = 0.5
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    OPENROUTER_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    GLOBAL_SPEND_FILE: str = "data/spend.json"

    IMAGE_TOKENS: int = 1600  # one 1280x800 screenshot
    CHARS_PER_TOKEN: float = 3.0  # pessimistic, so text is never under-estimated
    ACTOR_HISTORY_STEPS: int = 8
    ACTOR_MAX_OUTPUT_TOKENS: int = 1500

    DATE_FORMAT: str = "%d/%m/%Y"  # how resolved date parameters are typed into the app
    MIN_CALL_S: float = 4  # no model call starts with less time than this left to its deadline
    CONTINUITY_MAX_PIXELS: int = 300  # pixels allowed to differ between end of fill and Save
    OSCILLATION_FAIL_STEPS: int = 4  # steps of continued oscillation, after the warning
    QUESTION_MAX_VIEWS: int = 10  # distinct views kept as evidence for a question
    QUESTION_VIEW_SIZE: tuple[int, int] = (960, 600)
    TABLE_MAX_PAGES: int = 15
    VERIFY_MAX_STEPS: int = 10
    VERIFY_RESERVE_S: float = 10  # verification navigation stops this long before the hard deadline
    MAX_CORRECTABLE_DENIALS: int = 2  # per action; the next denial of the same action is final
    WAIT_ACTION_S: float = 1.0
    CLOSE_TIMEOUT_S: float = 2.0
    VERIFIER_MAX_EVIDENCE_SHOTS: int = 4
    TRACE_SHOT_SIZE: tuple[int, int] = (640, 400)

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config comes from environment variables / .env. No hard-coded secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://support:support@localhost:5434/support"
    log_format: str = "text"  # "json" in the production image: one JSON object per line
    log_level: str = "INFO"

    # Agent (Gemini), used from Phase 2. Free tier: ~20 requests/day on the main model and an
    # agent makes ~3-6 calls per ticket, so most eval runs use the lite model.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_s: int = 60
    gemini_thinking_level: str = "low"  # higher levels were 25-50x slower in the invoice project
    llm_retry_backoff_s: float = 2.0  # transient errors: wait 2s, 4s before retrying the same model

    # Classification is an easy task: run it on the lite model and keep the main model's small daily
    # quota for the agent. The main model is the fallback.
    classify_model: str = "gemini-3.5-flash-lite"
    classify_fallback_model: str = "gemini-3.5-flash"
    classify_prompt_version: str = "classify_v1"

    # The agent loop (Phase 3): main model, lite as fallback. ~3-6 calls per ticket, so eval runs mostly
    # override AGENT_MODEL to the lite model (free tier: ~20 requests/day on the main model).
    agent_model: str = "gemini-3.5-flash"
    agent_fallback_model: str = "gemini-3.5-flash-lite"
    agent_prompt_version: str = "agent_v1"
    agent_max_steps: int = 6  # model turns per ticket; past it the ticket is failed (step_limit)
    agent_timeout_s: float = 120.0  # wall clock for the whole loop

    # Guardrails (Phase 4). Internal: never in a policy page or the prompt (rule 13). A refund or replacement
    # worth this much or more always needs a human, even with zero flags.
    auto_limit_cents: int = 5000

    # Public live demo (Phase 8). Off everywhere except the demo deployment: the /demo/* routes answer 404
    # unless DEMO_MODE=true.
    demo_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

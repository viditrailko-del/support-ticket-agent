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


@lru_cache
def get_settings() -> Settings:
    return Settings()

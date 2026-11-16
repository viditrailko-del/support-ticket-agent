import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.logging_setup import adopt_uvicorn_loggers, configure_logging, log_event

configure_logging(get_settings().log_format, get_settings().log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    adopt_uvicorn_loggers()  # uvicorn configured its loggers after our import
    s = get_settings()
    log_event(log, logging.INFO, "startup", model=s.gemini_model, gemini_key_set=bool(s.gemini_api_key))
    yield


app = FastAPI(
    title="Support Ticket Agent",
    description="Proposes actions for support tickets with tools; code checks decide what runs.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

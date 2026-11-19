import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api.demo import router as demo_router
from app.api.reviews import router as reviews_router
from app.api.tickets import router as tickets_router
from app.config import Settings, get_settings
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


app.include_router(tickets_router)
app.include_router(reviews_router)
app.include_router(demo_router)  # 404 unless DEMO_MODE


DEMO_PAGE = Path(__file__).parent / "static" / "demo.html"


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False, response_model=None)
def home(settings: Annotated[Settings, Depends(get_settings)]) -> FileResponse | RedirectResponse:
    """The demo page on the public demo; elsewhere the API docs."""
    if settings.demo_mode:
        return FileResponse(DEMO_PAGE, media_type="text/html")
    return RedirectResponse("/docs")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

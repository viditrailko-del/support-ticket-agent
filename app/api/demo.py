"""Demo routes. Thin: the work is in app/demo. Every route answers 404 unless DEMO_MODE is on."""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.client import LLMClient, get_llm
from app.config import Settings, get_settings
from app.db.session import get_db
from app.demo import live
from app.demo.replay import replay, scenarios
from app.demo.reset import reset_demo
from app.logging_setup import log_event
from app.schemas.demo import DemoStatus, LiveTicketIn
from app.schemas.tickets import TicketOut
from app.tickets import get_ticket, receive_ticket


def demo_enabled(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


log = logging.getLogger(__name__)
router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(demo_enabled)])

Db = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def admit_live(db: Session, request: Request, settings: Settings) -> Settings:
    """Take one live slot (or 429) and return the settings a live ticket runs with. Also used by POST /tickets
    while DEMO_MODE is on, so /docs can't get around the cap."""
    try:
        live.quota.take(db, client_ip(request), settings, datetime.now(UTC))
    except live.QuotaExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, e.message) from None
    return live.live_settings(settings)


@router.get("/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """The recorded scenarios a visitor can replay: the email and what it shows."""
    return [s.summary() for s in scenarios().values()]


@router.post("/replay/{name}", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def replay_scenario(name: str, db: Db, settings: Cfg) -> TicketOut:
    """Run a scenario through the real pipeline (tools, checks, execution) with the model's answers
    pre-recorded: no Gemini call. Replaces this scenario's earlier replay."""
    sc = scenarios().get(name)
    if sc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scenario")
    t = replay(db, sc, settings)
    return TicketOut.from_ticket(get_ticket(db, t.id))  # type: ignore[arg-type]


@router.get("/customers")
def list_customers(db: Db, settings: Cfg) -> list[dict[str, Any]]:
    """The customers a visitor can write as in live mode, with their orders."""
    return live.list_customers(db, settings)


@router.get("/status", response_model=DemoStatus)
def demo_status(db: Db, settings: Cfg) -> DemoStatus:
    used = live.quota.used_today(db, datetime.now(UTC))
    return DemoStatus(
        live_daily_limit=settings.demo_live_daily_limit, live_used_today=used,
        live_left_today=max(settings.demo_live_daily_limit - used, 0),
        live_per_ip_per_hour=settings.demo_live_per_ip_per_hour, live_model=settings.demo_live_model,
        shop_today=settings.shop_today.isoformat(),
    )


@router.post("/reset", include_in_schema=False)
def reset(db: Db, settings: Cfg, x_reset_token: Annotated[str, Header()] = "") -> dict[str, int]:
    """Nightly (GitHub Actions): back to the seeded shop. Off unless DEMO_RESET_TOKEN is set."""
    if not settings.demo_reset_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    if not hmac.compare_digest(x_reset_token.encode(), settings.demo_reset_token.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad reset token")
    result = reset_demo(db)
    live.quota.reset()  # re-read from the (now empty) DB on the next request
    log_event(log, logging.INFO, "demo_reset", **result)
    return result


@router.post("/live", response_model=TicketOut, status_code=status.HTTP_201_CREATED,
             responses={404: {"description": "Not a demo customer"}, 429: {"description": "Live quota used up"}})
def live_ticket(body: LiveTicketIn, request: Request, db: Db, settings: Cfg,
                llm: Annotated[LLMClient, Depends(get_llm)]) -> TicketOut:
    """Your own email, sent as a demo customer, answered by real Gemini (flash-lite). The server sets the
    sender address and the received date (the shop's today). Capped per day and per visitor."""
    customer = live.demo_customer(db, body.customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not a demo customer: see GET /demo/customers")
    run_settings = admit_live(db, request, settings)
    t = receive_ticket(db, from_email=customer.email, subject=body.subject, body=body.body,
                       received_at=live.received_at(settings, datetime.now(UTC)), llm=llm, settings=run_settings)
    return TicketOut.from_ticket(t)

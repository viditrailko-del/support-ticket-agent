"""Demo routes. Thin: the work is in app/demo. Every route answers 404 unless DEMO_MODE is on."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.demo.replay import replay, scenarios
from app.schemas.tickets import TicketOut
from app.tickets import get_ticket


def demo_enabled(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(demo_enabled)])


@router.get("/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """The recorded scenarios a visitor can replay: the email and what it shows."""
    return [s.summary() for s in scenarios().values()]


@router.post("/replay/{name}", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def replay_scenario(
    name: str, db: Annotated[Session, Depends(get_db)], settings: Annotated[Settings, Depends(get_settings)]
) -> TicketOut:
    """Run a scenario through the real pipeline (tools, checks, execution) with the model's answers
    pre-recorded: no Gemini call. Replaces this scenario's earlier replay."""
    sc = scenarios().get(name)
    if sc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scenario")
    t = replay(db, sc, settings)
    return TicketOut.from_ticket(get_ticket(db, t.id))  # type: ignore[arg-type]

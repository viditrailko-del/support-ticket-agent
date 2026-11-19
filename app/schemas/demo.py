"""API models for /demo."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LiveTicketIn(BaseModel):
    """A visitor's own email, sent as one of the demo customers. The server sets the sender address and the
    received date; the body is shorter than POST /tickets allows, to keep each live call small."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^C\d{4}$", description="One of GET /demo/customers")
    subject: str = Field(max_length=200)
    body: str = Field(min_length=1, max_length=3000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body is empty (only whitespace)")
        return v


class DemoStatus(BaseModel):
    live_daily_limit: int
    live_used_today: int
    live_left_today: int
    live_per_ip_per_hour: int
    live_model: str
    shop_today: str

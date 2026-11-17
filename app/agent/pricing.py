"""What one LLM call cost, in USD.

Prices are per 1M tokens, standard (non-batch) tier, from
https://ai.google.dev/gemini-api/docs/pricing (as recorded in the invoice project, 2026-11-11; copied from rag_project).
Gemini bills thinking tokens as output, so they are added to the output count.

An unknown model gives cost None (not 0): "we don't know" must never look like "it was free".
"""

from decimal import Decimal

PER_MILLION = Decimal(1_000_000)

# model -> (input USD per 1M tokens, output USD per 1M tokens)
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # gemini-3.5-flash is deprecated; Google routes it to gemini-3.6-flash, billed at that rate.
    "gemini-3.5-flash": (Decimal("1.50"), Decimal("7.50")),
    "gemini-3.6-flash": (Decimal("1.50"), Decimal("7.50")),
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
}


def cost_usd(
    model: str, input_tokens: int | None, output_tokens: int | None, thinking_tokens: int | None = None
) -> Decimal | None:
    prices = PRICES.get(model)
    if prices is None or input_tokens is None or output_tokens is None:
        return None
    in_price, out_price = prices
    billed_output = output_tokens + (thinking_tokens or 0)
    return ((input_tokens * in_price + billed_output * out_price) / PER_MILLION).quantize(Decimal("0.000001"))

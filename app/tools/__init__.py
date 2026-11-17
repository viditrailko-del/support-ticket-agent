"""The agent's tools: plain DB functions, no LLM. Every tool is scoped to the ticket's sender.

The model chooses which tool to call and writes its arguments, but it never chooses WHOSE data it sees:
the customer comes from ToolContext (matched from the ticket's sender address when the ticket was stored),
not from anything the model or the email says. An order that exists but belongs to someone else looks
exactly like an order that doesn't exist, so the tools never leak another customer's data (rule 1).
"""

from app.tools.registry import TOOLS, ToolContext, run_tool

__all__ = ["TOOLS", "ToolContext", "run_tool"]

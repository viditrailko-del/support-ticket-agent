"""Policy pages -> rules with stable ids.

Each page in data/policies/*.md is `# Title`, an optional intro, then one `## <RULE-ID> <rule title>` heading
per rule followed by the rule text. The rule id is what the agent cites and what labels point at; a citation
is valid only if its quote is an exact substring of that one rule's text.

Pure: reads files, no DB, no network.
"""

import re
from dataclasses import dataclass
from pathlib import Path

POLICY_DIR = Path(__file__).with_name("policies")
_RULE_HEADING = re.compile(r"^## ([A-Z]{3}-\d+) (.+)$")


@dataclass(frozen=True)
class Rule:
    id: str  # e.g. "REF-1"
    page: str  # file stem, e.g. "refunds"
    page_title: str
    title: str
    text: str


def parse_page(page: str, markdown: str) -> list[Rule]:
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{page}: first line must be '# Title'")
    page_title = lines[0][2:].strip()
    rules: list[Rule] = []
    current: tuple[str, str] | None = None
    body: list[str] = []

    def flush() -> None:
        if current:
            text = " ".join(l.strip() for l in body if l.strip())
            if not text:
                raise ValueError(f"{page}: rule {current[0]} has no text")
            rules.append(Rule(current[0], page, page_title, current[1].strip(), text))

    for line in lines[1:]:
        if line.startswith("## "):
            m = _RULE_HEADING.match(line)
            if not m:
                raise ValueError(f"{page}: bad rule heading {line!r} (want '## ABC-1 Title')")
            flush()
            current, body = (m.group(1), m.group(2)), []
        elif current:
            body.append(line)
    flush()
    return rules


def load_rules(policy_dir: Path = POLICY_DIR) -> dict[str, Rule]:
    """All rules by id. Raises on a duplicate id."""
    rules: dict[str, Rule] = {}
    for path in sorted(policy_dir.glob("*.md")):
        for rule in parse_page(path.stem, path.read_text(encoding="utf-8")):
            if rule.id in rules:
                raise ValueError(f"duplicate rule id {rule.id} in {path.name}")
            rules[rule.id] = rule
    return rules

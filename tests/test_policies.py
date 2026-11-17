import pytest

from data.policies import load_rules, parse_page


def test_all_pages_parse_with_unique_ids():
    rules = load_rules()
    assert len({r.page for r in rules.values()}) == 10
    assert "REF-1" in rules and rules["REF-1"].page == "refunds"
    assert "30 days" in rules["REF-1"].text


def test_rule_text_joins_lines_and_excludes_heading():
    rules = parse_page("x", "# X\n\nintro\n\n## ABC-1 First\nline one\nline two\n\n## ABC-2 Second\nmore\n")
    assert [(r.id, r.title, r.text) for r in rules] == [
        ("ABC-1", "First", "line one line two"),
        ("ABC-2", "Second", "more"),
    ]
    assert rules[0].page_title == "X"


@pytest.mark.parametrize("bad", [
    "no title\n## ABC-1 T\ntext",
    "# T\n## abc-1 lowercase id\ntext",
    "# T\n## ABC-1 Empty\n\n## ABC-2 Next\ntext",
])
def test_malformed_pages_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_page("bad", bad)

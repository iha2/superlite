from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
Q = "Run the whole test suite at finish?"
NO = "No, affected tests only (Recommended)"
YES = "Yes, once at finish"


def read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def test_inventing_asks():
    t = read("skills/inventing/SKILL.md")
    assert Q in t and NO in t and YES in t
    assert "never a separate question round" in t
    assert "in-chat design" in t


def test_ai_ruling():
    t = read("skills/inventing/SKILL.md")
    assert "Ruling: full suite no (because affected runs cover the change and the user was not asked)" in t


def test_drafting_header():
    t = read("skills/drafting/SKILL.md")
    assert "`**Full suite:** yes | no`" in t and "missing means no" in t


def test_modes_mention():
    for m in ("feature", "debug", "mvp", "review"):
        assert Q in read(f"commands/{m}.md"), m
    s = read("commands/small.md")
    assert "full suite" in s and "no" in s.split("full suite", 1)[1][:80]


def test_review_plan_decision():
    t = read("skills/drafting/SKILL.md") + read("skills/inventing/SKILL.md")
    assert "Decisions you can change" in t and Q in t

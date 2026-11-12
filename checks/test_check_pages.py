import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

FX = HERE / "fixtures"
MAIN = str(FX / "main")
REPO = str(HERE.parent.parent)

GUIDE_TAIL = "\n## What you do\n\nx\n\n## What superlite does\n\ny\n\n## Example\n\nz\n"


def _site(tmp_path, group, name, body):
    d = tmp_path / "docs" / group
    d.mkdir(parents=True)
    (d / name).write_text("---\ntitle: T\n---\n\n" + body)
    return str(tmp_path / "docs")


def test_real_names():
    n = check_pages.real_names(REPO)
    assert "start" in n["command"]
    assert "session" in n["tool"]
    assert "inventing" in n["skill"] and "implementer" in n["skill"]


def test_good_ok():
    assert check_pages.check(str(FX / "good"), MAIN) == []


def test_bad_problems():
    assert check_pages.check(str(FX / "bad"), MAIN) == [
        "flows/x.md: no diagram first",
        "flows/x.md: 412 prose words (max 300)",
        "flows/x.md: /superlite:nope not a command",
        "flows/x.md: link /superlite/missing/ not found",
    ]


def test_prose_ignores_fences_and_tables(tmp_path):
    big = "```bash\n" + "w " * 500 + "\n```\n\n| a |\n|---|\n| " + "w " * 500 + " |\n"
    docs = _site(tmp_path, "run", "p.md", "```text\nd\n```\n" + big + GUIDE_TAIL)
    assert check_pages.check(docs, MAIN) == []


def test_link_with_anchor_ok(tmp_path):
    docs = _site(
        tmp_path, "run", "report.md",
        "```text\nd\n```\n[a](/superlite/run/report/#example)" + GUIDE_TAIL,
    )
    assert check_pages.check(docs, MAIN) == []


def test_flow_screen_mismatch(tmp_path):
    docs = _site(tmp_path, "flows", "regular.md", "```text\nother\n```\n" + GUIDE_TAIL)
    assert check_pages.check(docs, MAIN) == [
        "flows/regular.md: flow screen differs from assets/flows/regular.txt"
    ]


def test_reference_page_no_diagram_ok(tmp_path):
    docs = _site(tmp_path, "reference", "cli.md", "| a | b |\n|---|---|\n| 1 | 2 |\n\nSome text.\n")
    assert check_pages.check(docs, MAIN) == []


def test_unknown_tool_and_skill(tmp_path):
    docs = _site(tmp_path, "reference", "c.md", "`superlite-cli zzz` and `superlite:nada`\n")
    got = check_pages.check(docs, MAIN)
    assert "reference/c.md: superlite-cli zzz not a tool" in got
    assert "reference/c.md: superlite:nada not a skill or agent" in got

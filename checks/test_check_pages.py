import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

FX = HERE / "fixtures"
MAIN = str(FX / "main")

GUIDE_TAIL = "\n## What you do\n\nx\n\n## What superlite does\n\ny\n\n## Example\n\nz\n"


def _site(tmp_path, group, name, body):
    d = tmp_path / "docs" / group
    d.mkdir(parents=True)
    (d / name).write_text("---\ntitle: T\n---\n\n" + body)
    return str(tmp_path / "docs")


def test_real_names():
    n = check_pages.real_names(MAIN)
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


def test_resolve_main_order(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERLITE_MAIN", raising=False)
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setattr(check_pages, "SITE", site)
    assert check_pages.resolve_main() == tmp_path
    (site / "_main").mkdir()
    assert check_pages.resolve_main() == site / "_main"
    monkeypatch.setenv("SUPERLITE_MAIN", "/env/main")
    assert check_pages.resolve_main() == Path("/env/main")
    assert check_pages.resolve_main("/arg/main") == Path("/arg/main")


def test_cli_rejects_missing_main(tmp_path, capsys):
    docs = _site(tmp_path, "reference", "c.md", "text\n")
    sys.argv = ["check_pages", "--docs", docs, "--main", str(tmp_path / "nope")]
    assert check_pages.main() == 2
    assert "no main checkout" in capsys.readouterr().err


def _ref(tmp_path, name, rows):
    body = "| Name | Job |\n|---|---|\n" + "".join(f"| `{r}` | x |\n" for r in rows)
    return _site(tmp_path, "reference", name, body)


def test_reference_skill_names_validated(tmp_path):
    docs = _ref(tmp_path, "skills.md", ["inventing", "gone-skill"])
    assert check_pages.check(docs, MAIN) == ["reference/skills.md: gone-skill not a skill on main"]


def test_reference_agent_names_validated(tmp_path):
    docs = _ref(tmp_path, "agents.md", ["implementer", "ghost"])
    assert check_pages.check(docs, MAIN) == ["reference/agents.md: ghost not an agent on main"]


def test_reference_agent_table_rejects_skill_name(tmp_path):
    docs = _ref(tmp_path, "agents.md", ["inventing"])
    assert check_pages.check(docs, MAIN) == ["reference/agents.md: inventing not an agent on main"]


def _index(tmp_path, front, body):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "index.mdx").write_text("---\ntitle: T\n" + front + "---\n\n" + body)
    return str(d)


def test_index_links_and_names_checked(tmp_path):
    docs = _index(
        tmp_path,
        "hero:\n  actions:\n    - text: Go\n      link: /superlite/start/gone/\n",
        '<LinkCard href="/superlite/missing/" />\n[b](/superlite/other/)\n`/superlite:nope`\n',
    )
    assert check_pages.check(docs, MAIN) == [
        "index.mdx: /superlite:nope not a command",
        "index.mdx: link /superlite/missing/ not found",
        "index.mdx: link /superlite/other/ not found",
        "index.mdx: link /superlite/start/gone/ not found",
    ]


def test_index_skips_diagram_and_length_checks(tmp_path):
    docs = _index(tmp_path, "", "w " * 500 + "\n")
    assert check_pages.check(docs, MAIN) == []


def test_index_good_href_and_hero_link_ok(tmp_path):
    docs = _index(tmp_path, "hero:\n  actions:\n    - link: /superlite/\n", '<LinkCard href="/superlite/" />\n')
    assert check_pages.check(docs, MAIN) == []

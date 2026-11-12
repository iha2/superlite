import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

FX = HERE / "fixtures"
MAIN = str(FX / "main")

TLDR = ":::tip[TL;DR]\nShort summary.\n:::\n\n"
GUIDE_TAIL = "\n## How it works\n\nx\n\n## Example\n\nz\n"


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
        "flows/x.md: no :::tip[TL;DR] aside first",
        "flows/x.md: no diagram before the first ## heading",
        "flows/x.md: no ## Example section",
        "flows/x.md: 935 prose words (max 900)",
        "flows/x.md: mermaid type pie not allowed",
        "flows/x.md: /superlite:nope not a command",
        "flows/x.md: link /superlite/missing/ not found",
    ]


def test_prose_ignores_fences_and_tables(tmp_path):
    big = "```bash\n" + "w " * 500 + "\n```\n\n| a |\n|---|\n| " + "w " * 500 + " |\n"
    docs = _site(tmp_path, "run", "p.md", TLDR + "```text\nd\n```\n" + big + GUIDE_TAIL)
    assert check_pages.check(docs, MAIN) == []


def test_prose_ignores_image_lines(tmp_path):
    img = "![" + "alt " * 600 + "](../../assets/screens/x.png)"
    body = TLDR + "```text\nd\n```\n1. Step\n   " + img + "\n" + img + "\n" + GUIDE_TAIL
    docs = _site(tmp_path, "run", "p.md", body)
    assert check_pages.check(docs, MAIN) == []


def test_link_with_anchor_ok(tmp_path):
    docs = _site(
        tmp_path, "run", "report.md",
        TLDR + "```text\nd\n```\n[a](/superlite/run/report/#example)" + GUIDE_TAIL,
    )
    assert check_pages.check(docs, MAIN) == []


def test_flow_screen_mismatch(tmp_path):
    docs = _site(tmp_path, "flows", "regular.md", TLDR + "```text\nother\n```\n" + GUIDE_TAIL)
    assert check_pages.check(docs, MAIN) == [
        "flows/regular.md: flow screen differs from assets/flows/regular.txt"
    ]


def test_reference_page_no_diagram_ok(tmp_path):
    docs = _site(tmp_path, "reference", "cli.md", TLDR + "| a | b |\n|---|---|\n| 1 | 2 |\n\nSome text.\n")
    assert check_pages.check(docs, MAIN) == []


def test_unknown_tool_and_skill(tmp_path):
    docs = _site(tmp_path, "reference", "c.md", TLDR + "`superlite-cli zzz` and `superlite:nada`\n")
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
    docs = _site(tmp_path, "reference", "c.md", TLDR + "text\n")
    sys.argv = ["check_pages", "--docs", docs, "--main", str(tmp_path / "nope")]
    assert check_pages.main() == 2
    assert "no main checkout" in capsys.readouterr().err


def _ref(tmp_path, name, rows):
    body = TLDR + "| Name | Job |\n|---|---|\n" + "".join(f"| `{r}` | x |\n" for r in rows)
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


DIAGRAM = "```text\nd\n```\n"


def test_900_words_ok_901_fails(tmp_path):
    def page(n):
        return TLDR + DIAGRAM + "\n## How it works\n\n" + " ".join(["w"] * n) + "\n\n## Example\n"
    # the TL;DR aside counts 3 words (opening line and 2 words), so n body words make n + 3
    ok = _site(tmp_path / "a", "run", "p.md", page(897))
    assert check_pages.check(ok, MAIN) == []
    over = _site(tmp_path / "b", "run", "p.md", page(898))
    assert check_pages.check(over, MAIN) == ["run/p.md: 901 prose words (max 900)"]


def test_no_tldr(tmp_path):
    docs = _site(tmp_path, "reference", "c.md", "Some text.\n")
    assert check_pages.check(docs, MAIN) == ["reference/c.md: no :::tip[TL;DR] aside first"]


def test_tldr_not_first_or_wrong_title(tmp_path):
    late = _site(tmp_path / "a", "reference", "c.md", "Intro.\n\n" + TLDR)
    assert check_pages.check(late, MAIN) == ["reference/c.md: no :::tip[TL;DR] aside first"]
    other = _site(tmp_path / "b", "reference", "c.md", ":::tip[Summary]\nx\n:::\n")
    assert check_pages.check(other, MAIN) == ["reference/c.md: no :::tip[TL;DR] aside first"]


def test_tldr_after_imports_ok(tmp_path):
    imp = 'import { Steps } from "@astrojs/starlight/components";\nimport X from "./x";\n\n'
    docs = _site(tmp_path, "reference", "c.mdx", imp + TLDR + "Text.\n")
    assert check_pages.check(docs, MAIN) == []


def test_tldr_not_closed(tmp_path):
    docs = _site(tmp_path, "reference", "c.md", ":::tip[TL;DR]\nShort.\n\n## Next\n")
    assert check_pages.check(docs, MAIN) == ["reference/c.md: TL;DR aside not closed"]


def test_tldr_over_60_words(tmp_path):
    def page(n):
        return ":::tip[TL;DR]\n" + " ".join(["w"] * n) + "\n:::\n"
    ok = _site(tmp_path / "a", "reference", "c.md", page(60))
    assert check_pages.check(ok, MAIN) == []
    over = _site(tmp_path / "b", "reference", "c.md", page(61))
    assert check_pages.check(over, MAIN) == ["reference/c.md: TL;DR over 60 words"]


def test_guide_needs_diagram_before_first_heading(tmp_path):
    late = _site(tmp_path / "a", "start", "p.md", TLDR + "## Intro\n\n" + DIAGRAM + "\n## Example\n\nz\n")
    assert check_pages.check(late, MAIN) == ["start/p.md: no diagram before the first ## heading"]
    mer = _site(tmp_path / "b", "start", "p.md", TLDR + "```mermaid\nflowchart LR\n  A --> B\n```\n" + GUIDE_TAIL)
    assert check_pages.check(mer, MAIN) == []


def test_heading_in_fence_does_not_end_the_intro(tmp_path):
    docs = _site(tmp_path, "start", "p.md", TLDR + "```text\n## not a heading\n```\n" + GUIDE_TAIL)
    assert check_pages.check(docs, MAIN) == []


def test_guide_needs_example_section(tmp_path):
    docs = _site(tmp_path, "sessions", "p.md", TLDR + DIAGRAM + "\n## How it works\n\nx\n")
    assert check_pages.check(docs, MAIN) == ["sessions/p.md: no ## Example section"]
    named = _site(tmp_path / "b", "sessions", "p.md", TLDR + DIAGRAM + "\n## Example: a CSV export\n\nx\n")
    assert check_pages.check(named, MAIN) == []


def test_old_fixed_headings_not_required(tmp_path):
    docs = _site(tmp_path, "run", "p.md", TLDR + DIAGRAM + "\n## Example\n\nz\n\n## Anything\n\nw\n")
    assert check_pages.check(docs, MAIN) == []


def test_reference_page_needs_no_diagram_or_example(tmp_path):
    docs = _site(tmp_path, "results", "p.md", TLDR + "Text.\n")
    assert check_pages.check(docs, MAIN) == []


def test_mermaid_types(tmp_path):
    for i, t in enumerate(["flowchart TD", "sequenceDiagram", "stateDiagram-v2", "gitGraph"]):
        docs = _site(tmp_path / str(i), "reference", "c.md", TLDR + f"```mermaid\n{t}\n  A --> B\n```\n")
        assert check_pages.check(docs, MAIN) == [], t
    bad = _site(tmp_path / "bad", "reference", "c.md", TLDR + "```mermaid\ngantt\n  x\n```\n")
    assert check_pages.check(bad, MAIN) == ["reference/c.md: mermaid type gantt not allowed"]
    legacy = _site(tmp_path / "old", "reference", "c.md", TLDR + "```mermaid\ngraph LR\n  A --> B\n```\n")
    assert check_pages.check(legacy, MAIN) == ["reference/c.md: mermaid type graph not allowed"]


def test_index_needs_no_tldr(tmp_path):
    docs = _index(tmp_path, "", "Welcome.\n")
    assert check_pages.check(docs, MAIN) == []

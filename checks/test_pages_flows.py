import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

REPO = check_pages.resolve_main()
needs_main = pytest.mark.skipif(
    not check_pages.is_main(REPO),
    reason=f"no main checkout at {REPO}: pass SUPERLITE_MAIN or run `git worktree add _main main`",
)
DOCS = HERE.parent / "src" / "content" / "docs"
FLOWS = DOCS / "flows"
PAGES = ["choose", "regular", "mvp", "feature", "debug", "small", "review", "research"]
SCREENED = ["regular", "mvp", "feature", "debug", "research"]
CONCEPT_LINKS = [
    "/superlite/sessions/sessions/",
    "/superlite/sessions/worktrees/",
    "/superlite/sessions/sandbox/",
    "/superlite/run/orchestrators/",
    "/superlite/run/plan-and-approval/",
    "/superlite/run/parallel-build/",
    "/superlite/run/reviews-and-fixes/",
    "/superlite/run/report/",
]


def page(name):
    return (FLOWS / f"{name}.mdx").read_text()


def body(name):
    return page(name).split("\n---\n", 1)[1].lstrip()


def before_first_h2(name):
    return re.split(r"^## ", body(name), maxsplit=1, flags=re.M)[0]


def test_all_flow_pages_exist():
    for name in PAGES:
        assert (FLOWS / f"{name}.mdx").exists(), name


@needs_main
def test_flow_pages_pass_check():
    probs = check_pages.check(str(DOCS), str(REPO))
    assert [p for p in probs if p.startswith("flows/")] == []


@needs_main
def test_flow_screens_verbatim():
    for mode in SCREENED:
        want = (REPO / "assets" / "flows" / f"{mode}.txt").read_text().rstrip("\n")
        blocks = re.findall(r"```text\n(.*?)\n```", page(mode), re.S)
        assert want in blocks, mode


def test_tldr_first_and_short():
    for name in PAGES:
        text = body(name)
        lines = [l for l in text.split("\n") if l.strip() and not l.startswith("import ")]
        assert lines[0] == ":::tip[TL;DR]", name
        end = lines.index(":::")
        words = len(" ".join(lines[1:end]).split())
        assert 0 < words <= 60, (name, words)


def test_screen_before_first_heading():
    for mode in SCREENED:
        head = before_first_h2(mode)
        assert "```text" in head, mode
        assert head.index(":::tip[TL;DR]") < head.index("```text"), mode


def test_small_review_choose_have_mermaid_before_first_heading():
    for mode in ("small", "review", "choose"):
        assert "```mermaid" in before_first_h2(mode), mode


def test_mermaid_types_allowed():
    allowed = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gitGraph")
    for name in PAGES:
        for block in re.findall(r"```mermaid\n(.*?)\n```", page(name), re.S):
            assert block.lstrip().startswith(allowed), name


def test_every_flow_page_teaches_with_an_example():
    for name in PAGES:
        heads = re.findall(r"^## (.+)$", body(name), re.M)
        assert any(h.startswith("Example") for h in heads), name
        assert "How it works" in heads, name
        assert "Good to know" in heads, name


def test_example_is_a_transcript():
    for name in PAGES:
        example = re.split(r"^## Example[^\n]*\n", body(name), flags=re.M)[1]
        example = re.split(r"^## ", example, flags=re.M)[0]
        blocks = re.findall(r"```text\n(.*?)\n```", example, re.S)
        assert blocks, name
        assert any(re.search(r"^you +", b, re.M) and re.search(r"^superlite +", b, re.M) for b in blocks), name


def test_prose_budget():
    for name in PAGES:
        text = re.sub(r"```.*?```", "", body(name), flags=re.S)
        prose = [l for l in text.split("\n") if not l.lstrip().startswith(("|", "#", "import "))]
        assert len(" ".join(prose).split()) <= 900, name


def test_flow_pages_link_concept_homes_not_reexplain():
    linked = set()
    for name in PAGES:
        linked |= {l for l in CONCEPT_LINKS if l in page(name)}
    assert linked == set(CONCEPT_LINKS)


def test_facts_per_flow():
    # facts that hold in the code (commands/*.md, assets/flows)
    assert "at most 8 tasks" in page("feature") or "8 tasks" in page("feature")
    assert "proto discard" in page("feature")
    assert "reply approves" in page("mvp").lower() or "your reply approves" in page("mvp").lower()
    assert "mvp-feedback.json" in page("mvp")
    assert "superlite-cli suggest_flow" in page("choose")
    assert "--level" in page("research")
    assert "Tier 1" in page("debug") and "Opus" in page("debug")
    assert "superlite-cli finish" in page("small")
    assert "never edits" in page("review") or "edits nothing" in page("review")


def test_sidebar_order_unique():
    orders = []
    for name in PAGES:
        m = re.search(r"order:\s*(\d+)", page(name))
        assert m, name
        orders.append(int(m.group(1)))
    assert len(set(orders)) == len(orders)

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

REPO = HERE.parent.parent
DOCS = HERE.parent / "src" / "content" / "docs"
FLOWS = DOCS / "flows"
PAGES = ["choose", "regular", "mvp", "feature", "debug", "small", "review", "research"]
SCREENED = ["regular", "mvp", "feature", "debug", "research"]


def test_all_flow_pages_exist():
    for name in PAGES:
        assert (FLOWS / f"{name}.md").exists(), name


def test_flow_pages_pass_check():
    probs = check_pages.check(str(DOCS), str(REPO))
    assert [p for p in probs if p.startswith("flows/")] == []


def test_flow_screens_verbatim():
    for mode in SCREENED:
        want = (REPO / "assets" / "flows" / f"{mode}.txt").read_text().rstrip("\n")
        text = (FLOWS / f"{mode}.md").read_text()
        blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
        assert want in blocks, mode


def test_screen_is_first_block():
    for mode in SCREENED:
        body = (FLOWS / f"{mode}.md").read_text().split("\n---\n", 1)[1].lstrip()
        assert body.startswith("```text"), mode


def test_small_and_review_open_with_mermaid():
    for mode in ("small", "review", "choose"):
        body = (FLOWS / f"{mode}.md").read_text().split("\n---\n", 1)[1].lstrip()
        assert body.startswith("```mermaid"), mode


def test_sidebar_order_unique():
    orders = []
    for name in PAGES:
        m = re.search(r"order:\s*(\d+)", (FLOWS / f"{name}.md").read_text())
        assert m, name
        orders.append(int(m.group(1)))
    assert len(set(orders)) == len(orders)

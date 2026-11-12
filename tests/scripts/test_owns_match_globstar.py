import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
from next_tasks import owns_match  # noqa: E402


def test_trailing_globstar_any_depth():
    assert owns_match("a/x", ["a/**"])
    assert owns_match("a/b/src/m.py", ["a/b/**"])
    assert owns_match("a/x/y/z.py", ["a/**"])


def test_leading_globstar_matches_root():
    assert owns_match("c.py", ["**/c.py"])
    assert owns_match("x/y/c.py", ["**/c.py"])


def test_single_star_one_segment():
    assert owns_match("a/x", ["a/*"])
    assert not owns_match("a/x/y", ["a/*"])

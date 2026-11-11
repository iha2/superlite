from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def test_swarm_no_wait():
    s = read("skills/swarm/SKILL.md")
    assert "run_tests --wait" not in s
    final = s.split("## Final review")[1].split("## Measure")[0]
    assert "run_tests --affected --background" in final
    batching = s.split("## Coordinator batching")[1].split("## Final review")[0]
    assert "never waits on a test run" in batching


def test_task_verify_affected():
    s = read("skills/swarm/SKILL.md")
    assert "run_tests --affected --files <owned files> --background" in s
    assert "--wait --run" in s
    for p in ("skills/swarm/fixing.md", "skills/swarm/implementing.md", "agents/package-reviewer.md"):
        assert "run_tests --affected --files <" in read(p)
        assert "--fast PATHS" in read(p)
        assert "--wait --run" in read(p)


def test_testing_no_wave_full():
    assert "wave's" not in read("skills/testing/SKILL.md")


def test_shipping_full_suite_choice():
    s = read("skills/shipping/SKILL.md")
    assert "**Full suite:**" in s
    assert "run_tests --full --reuse" in s
    assert "run_tests --affected --base <start sha>" in s
    assert "Red blocks merge and PR" in s


def test_proof_cites_run():
    assert "naming which finish run applied" in read("skills/proof/SKILL.md")


def test_tools_doc():
    s = read("docs/tools.md")
    assert "--affected" in s and "--reuse" in s


def test_reports_carry_run_id():
    for p in ("skills/swarm/fixing.md", "skills/swarm/implementing.md", "agents/package-reviewer.md"):
        assert "tests: <passed> <failed> <skipped> run <id>" in read(p)
    s = read("skills/swarm/SKILL.md")
    assert "tests: <passed> <failed> <skipped> run <id>" in s
    assert "missing or mismatched id is unverified" in s
    assert len(s) <= 12500

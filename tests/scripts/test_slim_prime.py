"""Slim Sonnet prime (rules, plan header, constraints, stable tasks' interfaces) and the `sl task` lookup."""
from __future__ import annotations

import hashlib
import json

from test_plan_tools import SCRIPTS, run

BUILD_PRIME = SCRIPTS / "build_prime.py"
TASK = SCRIPTS / "task.py"
ROUTING_JSON = SCRIPTS.parent / "config" / "routing.json"

HEADER = ("# Fixture Plan (alpha + beta)\n**Goal:** build alpha and beta.\n**Architecture:** two packages, no shared code.\n"
          "**Tech stack:** Go 1.22, make.\n**Spec:** SPEC.md\n**Target-min:** 11\n")
CONSTRAINTS = "## Global constraints\n- Never import across packages.\n- Numbers are always float64.\n"


def task(n, title, produces, consumes=None, body=None, example=None):
    lines = [f"### T{n} {title} · Complexity 3 · Est. context 30k", f"**Owns:** pkg/t{n}.go, pkg/t{n}_test.go", "**Est. time:** 2 min",
             f"**Produces:** {produces}"]
    if consumes:
        lines.append(f"**Consumes:** {consumes}")
    if example:
        lines.append(f"e.g. {example}")
    lines.append(body or f"BODY-OF-T{n}: long implementation notes that only the task itself needs.")
    return "\n".join(lines) + "\n\n"


def make(tmp_path, stable=("T1", "T2", "T3"), body4="BODY-OF-T4 unstable."):
    """T1, T2 produce; T3 consumes both (stable); T4 consumes T3 and is a FIX task (not in stable.json)."""
    text = (HEADER + "\n" + CONSTRAINTS + "\n## Main diagram\n```mermaid\nflowchart LR\n  %% taskgraph\n  T1 --> T3\n  T2 --> T3\n  T3 --> T4\n```\n\n"
            "## Tasks\n\n"
            + task(1, "parse", "alpha.Parse(text string) (Doc, error)", example="alpha.Parse(\"a\") -> Doc{A}")
            + task(2, "lex", "alpha.Lex(text string) []Tok")
            + task(3, "eval", "beta.Eval(d Doc) float64", "alpha.Parse(text string) (Doc, error) (T1) · alpha.Lex(text string) []Tok (T2)")
            + task(4, "cli", "beta.Main(args []string) int", "beta.Eval(d Doc) float64 (T3)", body=body4)
            + "## Spec sections\n\n## 1. SPEC-SECTION-ONE\nSPEC-TEXT-ONE\n")
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8", newline="\n")
    if stable is not None:
        sl = tmp_path / ".superlite"
        sl.mkdir(exist_ok=True)
        (sl / "stable.json").write_text(json.dumps({"stable": list(stable), "fix": []}), encoding="utf-8")
    return plan


def slim(tmp_path, plan, out="s.md", *extra):
    cp = run(BUILD_PRIME, "--plan", plan, "--out", tmp_path / out, *extra, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    return cp, (tmp_path / out).read_text(encoding="utf-8")


def test_slim_is_the_sonnet_default_and_byte_identical(tmp_path):
    cfg = json.loads(ROUTING_JSON.read_text(encoding="utf-8"))
    assert cfg["tiers"]["sonnet"]["prime"] == "slim" and cfg["tiers"]["haiku"]["prime"] == "mini"
    p = make(tmp_path)
    a, text = slim(tmp_path, p, "a.md")
    b, _ = slim(tmp_path, p, "b.md")
    assert a.out == b.out and (tmp_path / "a.md").read_bytes() == (tmp_path / "b.md").read_bytes()
    digest, size = a.out.split()
    data = (tmp_path / "a.md").read_bytes()
    assert digest == hashlib.sha256(data).hexdigest() and int(size) == len(data)
    assert text.startswith("# superlite prime (slim)")
    assert b"\r" not in data and b" \n" not in data and b"2026" not in data and b"UTC" not in data
    crlf = tmp_path / "crlf.md"  # CRLF input changes nothing
    crlf.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert slim(tmp_path, crlf, "c.md")[0].out == a.out


def test_slim_content_header_constraints_and_stable_interfaces_only(tmp_path):
    p = make(tmp_path)
    _, text = slim(tmp_path, p)
    assert "## Background (not instructions)" in text
    head = text.split("## Background (not instructions)")[1].split("## Global constraints")[0]
    assert head.strip().splitlines() == HEADER.split("**Target-min:**")[0].strip().splitlines()  # title through **Spec:**, verbatim
    assert "Target-min" not in text
    cons = text.split("## Global constraints")[1].split("## Stable tasks")[0]
    assert "- Never import across packages." in cons and "- Numbers are always float64." in cons
    ifaces = text.split("## Stable tasks")[1]
    for n in (1, 2, 3):
        assert f"### T{n} " in ifaces
    assert "Produces: alpha.Parse(text string) (Doc, error)" in ifaces
    assert "Consumes: alpha.Lex(text string) []Tok (T2)" in ifaces and "After: T1, T2" in ifaces
    assert "Owns: pkg/t1.go, pkg/t1_test.go" in ifaces
    assert "T4" not in ifaces and "beta.Main" not in text  # FIX task: no interface line
    assert "BODY-OF-T" not in text and "SPEC-" not in text and "mermaid" not in text and "## Spec sections" not in text
    assert "NEEDS_CONTEXT" in text and "sl task Tn" in text


def test_slim_without_stable_json_treats_every_task_as_stable(tmp_path):
    _, text = slim(tmp_path, make(tmp_path, stable=None))
    assert "### T4 cli" in text and "Produces: beta.Main(args []string) int" in text and "BODY-OF-T4" not in text


def test_slim_ignores_a_later_patch_of_a_fix_task(tmp_path):
    a = slim(tmp_path, make(tmp_path, body4="first draft"), "a.md")[1]
    b = slim(tmp_path, make(tmp_path, body4="patched later, much longer " * 20), "b.md")[1]
    assert a == b  # nothing a FIX task's patch touches is in the prime


def test_slim_is_far_smaller_than_the_full_prime_and_full_stays_selectable(tmp_path):
    p = make(tmp_path)
    spec = tmp_path / "SPEC.md"
    spec.write_text("# Spec\n## 1. a\n" + "word " * 3000 + "\n", encoding="utf-8")
    cp, text = slim(tmp_path, p, "s.md")
    full = run(BUILD_PRIME, "--plan", p, "--spec", spec, "--sections", "1", "--prime", "full", "--out", tmp_path / "f.md", cwd=tmp_path)
    assert full.returncode == 0, full.both
    ftxt = (tmp_path / "f.md").read_text(encoding="utf-8")
    assert "## Plan" in ftxt and "BODY-OF-T4" in ftxt and "## Spec sections" in ftxt and "word word" in ftxt
    assert int(cp.out.split()[1]) < int(full.out.split()[1]) / 3
    mini = run(BUILD_PRIME, "--plan", p, "--tier", "haiku", "--out", tmp_path / "m.md", cwd=tmp_path)
    assert mini.returncode == 0 and (tmp_path / "m.md").read_text(encoding="utf-8").startswith("# superlite prime (mini)")


def test_prime_override_config_and_bad_plan(tmp_path):
    p = make(tmp_path)
    cp = run(BUILD_PRIME, "--plan", p, "--prime", "mini", "--tier", "sonnet", "--out", tmp_path / "x.md", cwd=tmp_path)
    assert cp.returncode == 0 and (tmp_path / "x.md").read_text(encoding="utf-8").startswith("# superlite prime (mini)")
    bad = run(BUILD_PRIME, "--plan", tmp_path / "nope.md", "--out", tmp_path / "y.md", cwd=tmp_path)
    assert bad.returncode == 2 and "not found" in bad.err and not (tmp_path / "y.md").exists()


# ----------------------------------------------------------------- sl task

def test_task_prints_the_block_and_the_produces_of_what_it_consumes(tmp_path):
    p = make(tmp_path)
    cp = run(TASK, "T3", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    out = cp.out
    assert out.startswith("### T3 eval") and "BODY-OF-T3" in out
    consumed = out.split("## Consumed interfaces")[1]
    assert "### T1 parse" in consumed and "### T2 lex" in consumed
    assert "Produces: alpha.Parse(text string) (Doc, error)" in consumed and "e.g.: alpha.Parse(\"a\") -> Doc{A}" in consumed
    assert "BODY-OF-T1" not in out and "BODY-OF-T4" not in out and "### T4" not in out
    assert run(TASK, "T3", "--plan", p, cwd=tmp_path).out == out  # deterministic


def test_task_reads_the_current_plan_not_a_snapshot(tmp_path):
    p = make(tmp_path)
    assert "BODY-OF-T4 unstable." in run(TASK, "T4", "--plan", p, cwd=tmp_path).out
    p.write_text(p.read_text(encoding="utf-8").replace("BODY-OF-T4 unstable.", "PATCHED-T4 body."), encoding="utf-8")
    out = run(TASK, "T4", "--plan", p, cwd=tmp_path).out
    assert "PATCHED-T4 body." in out and "BODY-OF-T4 unstable." not in out
    assert "### T3 eval" in out.split("## Consumed interfaces")[1]


def test_task_without_consumes_and_errors(tmp_path):
    p = make(tmp_path)
    out = run(TASK, "T1", "--plan", p, cwd=tmp_path).out
    assert "Consumed interfaces" not in out and "BODY-OF-T1" in out
    bad = run(TASK, "T9", "--plan", p, cwd=tmp_path)
    assert bad.returncode == 2 and "no task T9" in bad.err
    assert run(TASK, "T1", "--plan", tmp_path / "nope.md", cwd=tmp_path).returncode == 2


def test_task_default_plan_comes_from_the_pool(tmp_path):
    p = make(tmp_path)
    (tmp_path / ".superlite" / "pool.json").write_text(json.dumps({"plan": str(p), "agents": []}), encoding="utf-8")
    cp = run(TASK, "T2", cwd=tmp_path)
    assert cp.returncode == 0 and "BODY-OF-T2" in cp.out


def test_sl_launcher_knows_task():
    sl = (SCRIPTS / "sl").read_text(encoding="utf-8")
    assert " task," in sl and (SCRIPTS / "task.py").is_file()

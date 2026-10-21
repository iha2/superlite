"""Generated-folder handling: config/gitignore.json, the .gitignore wave0 writes, the never-blocking list in
checkpoint, the grouped out-of-scope incidents, the plan's `**Ignore:**` line, and the slim prime's ignored-paths list."""
from __future__ import annotations

import json
import sys
import subprocess

from test_plan_tools import CONFIG, SCRIPTS, make_plan, pt, run
from test_slim_prime import make as slim_plan, slim
from test_wave0 import COMMIT, TS, WAVE0, plan as wave0_plan

TABLE = json.loads((CONFIG / "gitignore.json").read_text(encoding="utf-8"))
LANGUAGES = ["TypeScript", "JavaScript", "Python", "Go", "Rust", "C#", "Java", "Ruby"]


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def init_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")


def write(root, rel, text="x\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def run_wave0(tmp_path, p):
    cp = run(WAVE0, "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    return (tmp_path / ".gitignore").read_text(encoding="utf-8") if (tmp_path / ".gitignore").exists() else None


# ------------------------------------------------------------------ the table

def test_table_is_data_keyed_like_wave0_langs():
    import wave0
    assert [n for n, _ in wave0.LANGS] == LANGUAGES
    assert TABLE["comment"] and ".superlite/" in TABLE["common"] and ".DS_Store" in TABLE["common"] and "Thumbs.db" in TABLE["common"]
    for lang in LANGUAGES:
        assert lang in TABLE and isinstance(TABLE[lang], list)
    assert {"node_modules/", "dist/", "coverage/"} <= set(TABLE["TypeScript"])
    assert {".venv/", "__pycache__/", ".pytest_cache/", ".mypy_cache/"} <= set(TABLE["Python"])
    assert "target/" in TABLE["Rust"] and {"bin/", "obj/"} <= set(TABLE["C#"]) and {".gradle/", "build/"} <= set(TABLE["Java"])
    assert "vendor/" not in TABLE["Go"]


# ------------------------------------------------------------------ wave0 writes the .gitignore

def test_wave0_appends_missing_entries_and_keeps_existing_lines(tmp_path):
    original = "# mine\n/build-output\nnode_modules\n*.secret\n!keep.secret"  # no trailing newline, node_modules without a slash
    (tmp_path / ".gitignore").write_bytes(original.encode())
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript, vitest", "x", TS)
    text = run_wave0(tmp_path, p)
    assert text.startswith(original + "\n")  # own lines untouched, same order
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines.count("node_modules") == 1 and "node_modules/" not in lines  # already covered
    for want in ("dist/", "coverage/", ".superlite/", ".DS_Store", "Thumbs.db"):
        assert lines.count(want) == 1
    assert "__pycache__/" not in lines and "target/" not in lines  # only the plan's languages
    meta = json.loads((tmp_path / ".superlite" / "briefs" / "wave0.json").read_text(encoding="utf-8"))
    assert meta["gitignore"] is True and "dist/" in meta["gitignore_added"]


def test_wave0_creates_the_gitignore_and_a_second_run_changes_nothing(tmp_path):
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript", "x", TS)
    first = run_wave0(tmp_path, p)
    assert first is not None and first.startswith("# superlite") and "node_modules/" in first
    before = (tmp_path / ".gitignore").read_bytes()
    run_wave0(tmp_path, p)
    assert (tmp_path / ".gitignore").read_bytes() == before


def test_wave0_keeps_crlf_files_crlf(tmp_path):
    (tmp_path / ".gitignore").write_bytes(b"out.log\r\n")
    p = wave0_plan(tmp_path, "**Tech stack:** Rust", "x", TS)
    run_wave0(tmp_path, p)
    raw = (tmp_path / ".gitignore").read_bytes()
    assert raw.startswith(b"out.log\r\n") and b"target/\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")


def test_wave0_brief_says_the_gitignore_exists(tmp_path):
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript", "x", TS)
    run_wave0(tmp_path, p)
    brief = (tmp_path / ".superlite" / "briefs" / "wave0.md").read_text(encoding="utf-8")
    assert "`.gitignore` already exists" in brief and "Do not rewrite it" in brief and "another generated folder" in brief


# ------------------------------------------------------------------ the plan's Ignore line

def test_header_ignore_parse(tmp_path):
    import plan_tools
    plain = make_plan(tmp_path, [{"id": "T1"}], header="")
    assert plan_tools.Plan.load(str(plain)).header_ignore() is None
    with_line = make_plan(tmp_path, [{"id": "T1"}], header="**Ignore:** node_modules/, dist/, `.env`, *.sqlite,  ", name="b.md")
    assert plan_tools.Plan.load(str(with_line)).header_ignore() == ["node_modules/", "dist/", ".env", "*.sqlite"]
    fenced = make_plan(tmp_path, [{"id": "T1"}], header="```\n**Ignore:** secret/\n```", name="c.md")
    assert plan_tools.Plan.load(str(fenced)).header_ignore() is None


def test_wave0_gitignore_is_the_union_of_languages_common_and_the_plan(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript\n**Ignore:** node_modules, .next/, .env, *.sqlite", "x", TS)
    text = run_wave0(tmp_path, p)
    lines = text.splitlines()
    for want in ("dist/", ".superlite/", ".next/", ".env", "*.sqlite"):
        assert lines.count(want) == 1
    assert lines.count("node_modules/") == 1 and "node_modules" not in lines
    before = (tmp_path / ".gitignore").read_bytes()
    run_wave0(tmp_path, p)
    assert (tmp_path / ".gitignore").read_bytes() == before


def test_lint_notes_a_missing_ignore_line_and_never_fails(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1"}], header="")
    cp = pt("lint", "--plan", p)
    notes = [ln for ln in cp.out.splitlines() if "**Ignore:**" in ln]
    assert cp.returncode == 0 and len(notes) == 1 and notes[0].startswith("NOTE: ")
    q = make_plan(tmp_path, [{"id": "T1"}], header="**Ignore:** .next/, .env", name="q.md")
    assert "**Ignore:**" not in pt("lint", "--plan", q).out


# ------------------------------------------------------------------- checkpoint

def ts_repo(tmp_path, plan_stack="**Tech stack:** TypeScript"):
    init_repo(tmp_path)
    p = wave0_plan(tmp_path, plan_stack, "x", TS)
    write(tmp_path, "src/slug.ts", "export const slugify = (t: string) => t;\n")
    write(tmp_path, "src/slug.test.ts", "// test\n")
    return p


def test_checkpoint_ignores_untracked_dependency_folders_without_a_gitignore(tmp_path):
    p = ts_repo(tmp_path)
    for i in range(60):
        write(tmp_path, f"node_modules/pkg{i % 6}/file{i}.js")
    write(tmp_path, "dist/out.js")
    assert not (tmp_path / ".gitignore").exists()
    cp = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert sorted(files) == ["src/slug.test.ts", "src/slug.ts"]
    assert not (tmp_path / ".superlite" / "incidents.jsonl").exists()


def test_checkpoint_never_blocks_on_the_plans_ignore_entries(tmp_path):
    p = ts_repo(tmp_path, "**Tech stack:** TypeScript\n**Ignore:** local-db/, .env, *.sqlite")
    write(tmp_path, "local-db/a.dat")
    write(tmp_path, ".env")
    write(tmp_path, "data/app.sqlite")
    cp = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    write(tmp_path, "other-dir/a.dat")  # not listed: still blocks
    write(tmp_path, "src/slug.ts", "export const slugify = (t: string) => t + '';\n")
    again = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert again.returncode == 1 and "out-of-scope other-dir/a.dat" in again.out and "local-db" not in again.out


def test_ignored_matches_every_language_but_not_source():
    import checkpoint
    for path in ("node_modules/a/b.js", "pkg/node_modules/x.js", "target/debug/x", ".venv/lib/x.py", "a/__pycache__/m.pyc",
                 "obj/Debug/x.dll", "bin/x", ".gradle/x", "vendor/bundle/gems/x.rb", ".superlite/progress.md", "x/.DS_Store", "lib.pyc"):
        assert checkpoint.ignored(path), path
    for path in ("src/slug.ts", "vendor/lib/x.go", "binary/x", "docs/dist.md", "src/node_modules_notes.md"):
        assert not checkpoint.ignored(path), path
    assert {".superlite/", "__pycache__/", "node_modules/", "target/"} <= set(checkpoint.IGNORED_PARTS)


def test_out_of_scope_incidents_are_one_row_per_top_level_directory(tmp_path):
    p = ts_repo(tmp_path)
    write(tmp_path, "stray.txt")
    for i in range(25):
        write(tmp_path, f"thirdparty/f{i:02d}.js")
    write(tmp_path, "docs/a.md")
    write(tmp_path, "docs/b.md")
    cp = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 1
    out = cp.out.splitlines()
    assert sum(ln.startswith("out-of-scope ") for ln in out) == 20 and "... and 8 more" in out  # 28 blocked files
    assert any(ln.startswith("hint: 25 blocked files under thirdparty/") and "add it to .gitignore" in ln for ln in out)
    assert not any("under docs/" in ln and ln.startswith("hint") for ln in out)
    rows = [json.loads(ln) for ln in (tmp_path / ".superlite" / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    by_file = {r["file"]: r for r in rows}
    assert sorted(by_file) == ["docs/", "stray.txt", "thirdparty/"] and len(rows) == 3
    tp = json.dumps(by_file["thirdparty/"])
    assert "25 files under thirdparty/" in tp
    assert "f04.js" in tp and "f05.js" not in tp  # the first 5 paths only
    assert "2 files under docs/" in json.dumps(by_file["docs/"])


# ------------------------------------------------------------------ slim prime

def test_slim_prime_carries_the_gitignore_and_stays_deterministic(tmp_path):
    p = slim_plan(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\r\n# keep\r\n.env   \r\n", encoding="utf-8", newline="")
    _, text = slim(tmp_path, p, "a.md")
    _, again = slim(tmp_path, p, "b.md")
    assert text == again
    heading = "## Git-ignored paths (never create source here, never edit .gitignore)"
    assert heading in text
    tail = text.split(heading, 1)[1]
    assert "node_modules/\n# keep\n.env" in tail and "\r" not in text
    assert text.index(heading) > text.index("## Stable tasks")


def test_slim_prime_omits_the_section_without_a_gitignore(tmp_path):
    _, text = slim(tmp_path, slim_plan(tmp_path), "a.md")
    assert "Git-ignored paths" not in text


def test_agent_files_carry_the_ignored_paths_rule():
    for name, first in (("implementer.md", "git-ignored paths list in your prime"),  # the slim prime carries the list; the mini prime does not
                        ("implementer-haiku.md", "Read the repo's `.gitignore` first")):
        body = (SCRIPTS.parents[2] / "agents" / name).read_text(encoding="utf-8")
        assert first in body and "DONE_WITH_CONCERNS" in body and "Never edit `.gitignore`" in body


# ------------------------------------------------------------------ a task's own Owns win over the ignore list

OWNING = [{"tid": "T1", "title": "bundle", "owns": "src/slug.ts, src/slug.test.ts, dist/bundle.js, bin/tool",
           "produces": "slug.slugify(text: string) -> string"}]


def owning_repo(tmp_path):
    init_repo(tmp_path)
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript", "x", OWNING)
    write(tmp_path, "src/slug.ts")
    write(tmp_path, "src/slug.test.ts")
    write(tmp_path, "dist/bundle.js")
    write(tmp_path, "bin/tool")
    return p


def test_task_owning_files_in_ignored_folders_keeps_them_without_a_gitignore(tmp_path):
    p = owning_repo(tmp_path)
    write(tmp_path, "dist/stale.js")  # same folders, owned by no task: invisible, never blocks
    write(tmp_path, "bin/other")
    write(tmp_path, "node_modules/a/b.js")
    cp = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert sorted(files) == ["bin/tool", "dist/bundle.js", "src/slug.test.ts", "src/slug.ts"]


def test_unowned_files_in_ignored_folders_do_not_block_even_when_neighbours_are_owned(tmp_path):
    p = owning_repo(tmp_path)
    (tmp_path / "src" / "slug.ts").unlink()
    (tmp_path / "src" / "slug.test.ts").unlink()
    (tmp_path / "dist" / "bundle.js").unlink()
    (tmp_path / "bin" / "tool").unlink()
    write(tmp_path, "dist/stale.js")
    write(tmp_path, "src/slug.ts")
    cp = run(COMMIT, "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert files == ["src/slug.ts"]


def test_run_bookkeeping_is_never_staged_even_if_a_task_owns_its_folder(tmp_path):
    import checkpoint
    keep = checkpoint.claims([".superlite/", "dist/"])
    assert keep(".superlite/progress.md") and keep("dist/a.js") and checkpoint.hard_ignored(".superlite/progress.md")
    assert not checkpoint.hard_ignored("dist/a.js") and not checkpoint.claims(["**/types.*"])("node_modules/x/types.d.ts")


def test_wave0_does_not_ignore_what_a_task_owns(tmp_path):
    p = wave0_plan(tmp_path, "**Tech stack:** TypeScript", "x", OWNING)
    lines = run_wave0(tmp_path, p).splitlines()
    assert "dist/" not in lines and "node_modules/" in lines and "coverage/" in lines
    before = (tmp_path / ".gitignore").read_bytes()
    run_wave0(tmp_path, p)
    assert (tmp_path / ".gitignore").read_bytes() == before


def test_autocommit_dirty_outside_ignores_dependency_folders_and_owned_ignored_files(tmp_path):
    import os
    import autocommit
    init_repo(tmp_path)
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        write(tmp_path, "dist/bundle.js")   # checkpointed by a task that owns it
        write(tmp_path, "dist/stale.js")    # owned by no task, ignored
        write(tmp_path, "node_modules/a/b.js")
        write(tmp_path, ".superlite/progress.md")
        assert autocommit.dirty_outside({"dist/bundle.js"}) == []
        write(tmp_path, "stray.txt")
        assert autocommit.dirty_outside({"dist/bundle.js"}) == ["stray.txt"]
    finally:
        os.chdir(old)


def test_checkpoint_apply_keeps_owned_ignored_files_and_is_not_refused_by_unowned_ones(tmp_path):
    import os
    env = {**os.environ, "SUPERLITE_AUTOCOMMIT": "0"}
    p = owning_repo(tmp_path)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base", "--allow-empty")  # HEAD exists; the files below are new work
    for f in ("src/slug.ts", "src/slug.test.ts", "dist/bundle.js", "bin/tool"):
        write(tmp_path, f, "changed\n")
    write(tmp_path, "dist/stale.js")
    cp = subprocess.run([sys.executable, str(COMMIT), "T1", "--plan", str(p)], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    ap = subprocess.run([sys.executable, str(SCRIPTS / "autocommit.py"), "apply"], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")
    assert ap.returncode == 0, ap.stdout + ap.stderr
    tracked = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert sorted(tracked) == ["bin/tool", "dist/bundle.js", "src/slug.test.ts", "src/slug.ts"]

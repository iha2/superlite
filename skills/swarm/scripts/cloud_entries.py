"""Per-run slash entries (attach/watch) for live cloud runs."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import state  # noqa: E402

MARKER = state.DIR_NAME + "-entry"
KINDS = ("attach", "watch")
SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def _label(run, me, admin):
    name, owner = str(run.get("name", "")), str(run.get("owner", ""))
    if not SAFE.match(name):
        return None
    if owner == me:
        return name
    if admin and SAFE.match(owner):
        return f"{owner}.{name}"
    return None


def ref_of(label: str) -> str:
    """The API reference for an entry label: `chris.login_fix` is `chris/login_fix`. Names never hold a dot."""
    return label.replace(".", "/", 1)


def _desc(run):
    parts = [str(run.get("status", "")), f"step {run.get('step', '?')}", str(run.get("agent", ""))]
    return " · ".join(" ".join(p.split()) for p in parts if p)


def _body(kind, label):
    verb = "Attach to" if kind == "attach" else "Watch"
    return (f"{verb} the live cloud run `{label}`. Run "
            f"`superlite-cli cloud {kind} {ref_of(label)}` and report its output.\n")


def _owned(skills_dir):
    if not skills_dir.is_dir():
        return []
    prefixes = tuple(f"superlite-{k}-" for k in KINDS)
    return [d for d in skills_dir.iterdir()
            if d.is_dir() and not d.is_symlink() and d.name.startswith(prefixes) and (d / MARKER).exists()]


def sync(runs: list[dict], skills_dir: Path, me: str, admin: bool) -> list[str]:
    skills_dir = Path(skills_dir)
    want = {}
    for run in runs:
        label = _label(run, me, admin)
        if label is None:
            continue
        for kind in KINDS:
            want[f"superlite-{kind}-{label}"] = (kind, label, run)
    skills_dir.mkdir(parents=True, exist_ok=True)
    for d in _owned(skills_dir):
        if d.name not in want:
            shutil.rmtree(d, ignore_errors=True)
    for folder, (kind, label, run) in want.items():
        d = skills_dir / folder
        if d.exists() and not (d / MARKER).exists():
            continue
        d.mkdir(exist_ok=True)
        (d / MARKER).write_text("", encoding="utf-8")
        (d / "SKILL.md").write_text(
            f"---\nname: superlite:{kind}-{label}\ndescription: {_desc(run)}\n"
            f"disable-model-invocation: true\n---\n\n{_body(kind, label)}",
            encoding="utf-8")
    return [f for f in want if (skills_dir / f / MARKER).exists()]


def remove_all(skills_dir: Path) -> int:
    owned = _owned(Path(skills_dir))
    for d in owned:
        shutil.rmtree(d, ignore_errors=True)
    return len(owned)

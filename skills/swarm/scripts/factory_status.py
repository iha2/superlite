"""Status line for plugin factories: wraps the user's status line and adds one segment per logged-in factory.

With only superlite-cloud logged in this is cloud_status byte for byte. Plugin runs get their own
attach/watch entries (marker .superlite-factory-entry); cloud entries are never touched.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import cloud_status  # noqa: E402
import factory_registry  # noqa: E402
import state  # noqa: E402

CLOUD = "superlite-cloud"
MARKER = f"{state.DIR_NAME}-factory-entry"
PLUGIN_ERRORS = factory_registry.PLUGIN_ERRORS
KINDS = ("attach", "watch")
SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
ENDED = ("done", "failed", "cancelled")


def _attach_path() -> Path:
    return cloud_status._home() / "attach.json"


def _attach_state() -> dict:
    try:
        d = json.loads(_attach_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) and d.get("run") else {}


def _clear_attach() -> None:
    try:
        _attach_path().unlink()
    except OSError:
        pass


def _attached_factory() -> str | None:
    try:
        import factory_hook
        return factory_hook.attached_factory()
    except PLUGIN_ERRORS:
        st = _attach_state()
        return (st.get("factory") or CLOUD) if st else None


def _plugin_names() -> tuple[list[str], int]:
    """(logged-in plugin factories, count of all logged in). Any registry failure means cloud only."""
    try:
        logged = factory_registry.logged_in()
        return [n for n in logged if n != CLOUD], len(logged)
    except PLUGIN_ERRORS:
        return [], 0


def _provider(name: str):
    return factory_registry.provider(name)


def _kind(name: str) -> str:
    try:
        return factory_registry.kind_of(name)
    except PLUGIN_ERRORS:
        return ""


def _label(name: str, many: bool) -> str:
    return f"{name}({_kind(name)})" if many and _kind(name) else name


def _cloud_part(attached_cloud: bool):
    """(segment, status dict or None) for superlite-cloud, exactly as cloud_status computes it."""
    client = cloud_status._make_client()
    if client is None:
        return "", None
    st = client.status()
    name = cloud_status._attached_name(client) if attached_cloud else None
    return cloud_status.segment(st, name), st


def _plugin_part(name: str, attached: bool, many: bool) -> tuple[str, dict]:
    """(segment, status) for one plugin factory. Detaches when the attached run ended or is gone."""
    p = _provider(name)
    st = p.status()
    runs = [r for r in st.get("runs") or [] if isinstance(r, dict)]
    new = next((f"new: {n.get('text', '')}" for n in st.get("notices") or [] if not n.get("seen")), "")
    label = _label(name, many)
    ref = None
    if attached:
        cur = _attach_state()
        ref = cur.get("run")
        try:
            t = p.task(cur.get("id") or ref)
            if t.get("status") in ENDED:
                _clear_attach()
                ref = None
        except PLUGIN_ERRORS as e:
            if getattr(e, "status", None) in (403, 404):
                _clear_attach()
                ref = None
    if ref:
        return " · ".join(x for x in (f"ATTACHED {label} {ref}" if many else f"ATTACHED {ref}", new) if x), st
    if new:
        return new, st
    for r in runs:
        if r.get("status") == "running":
            return (f"{label} {r.get('name')} running" if many else f"{r.get('name')} running"), st
    if runs:
        text = f"{runs[0].get('name')} {runs[0].get('status', '')}".strip()
        return (f"{label} {text}" if many else text), st
    return "", st


def _compute() -> tuple[str, dict, dict | None]:
    """(segment, {factory: status}, cloud status)."""
    plugins, total = _plugin_names()
    att = _attached_factory()
    cloud_st = None
    if not plugins:
        seg, cloud_st = _cloud_part(att == CLOUD)
        return seg, {}, cloud_st
    parts, statuses = [], {}
    try:
        seg, cloud_st = _cloud_part(att == CLOUD)
        parts.append(seg)
    except PLUGIN_ERRORS:
        pass
    for n in plugins:
        try:
            seg, statuses[n] = _plugin_part(n, att == n, total > 1)
            parts.append(seg)
        except PLUGIN_ERRORS:
            continue  # a broken plugin never breaks another factory
    return " · ".join(x for x in parts if x), statuses, cloud_st


def segment() -> str:
    return _compute()[0]


def _entry_body(kind: str, ref: str, label: str) -> str:
    verb = "Attach to" if kind == "attach" else "Watch"
    return (f"{verb} the live factory run `{label}`. Run "
            f"`superlite-cli factory {kind} {ref}` and report its output.\n")


def _owned(skills_dir: Path) -> list[Path]:
    if not skills_dir.is_dir():
        return []
    prefixes = tuple(f"superlite-{k}-" for k in KINDS)
    return [d for d in skills_dir.iterdir()
            if d.is_dir() and not d.is_symlink() and d.name.startswith(prefixes) and (d / MARKER).exists()]


def _write_entries(statuses: dict, skills_dir: Path) -> list[str]:
    skills_dir = Path(skills_dir)
    want = {}
    for fname, st in statuses.items():
        for r in st.get("runs") or []:
            rname = str(r.get("name", ""))
            if not (SAFE.match(fname) and SAFE.match(rname)):
                continue
            for kind in KINDS:
                want[f"superlite-{kind}-{fname}-{rname}"] = (kind, fname, rname, r)
    if want:
        skills_dir.mkdir(parents=True, exist_ok=True)
    for d in _owned(skills_dir):
        if d.name not in want:
            shutil.rmtree(d, ignore_errors=True)
    for folder, (kind, fname, rname, r) in want.items():
        d = skills_dir / folder
        if d.exists() and not (d / MARKER).exists():
            continue
        d.mkdir(exist_ok=True)
        (d / MARKER).write_text("", encoding="utf-8")
        desc = json.dumps(" ".join(f"{r.get('status', '')} {r.get('title', '')}".split()))
        (d / "SKILL.md").write_text(
            f"---\nname: superlite:{kind}-{fname}-{rname}\ndescription: {desc}\n"
            f"disable-model-invocation: true\n---\n\n{_entry_body(kind, f'{fname}:{rname}', f'{fname}:{rname}')}",
            encoding="utf-8")
    return [f for f in want if (skills_dir / f / MARKER).exists()]


def clear_entries(skills_dir: Path) -> None:
    _write_entries({}, Path(skills_dir))


def sync_entries(skills_dir: str) -> list[str]:
    statuses = {}
    for n in _plugin_names()[0]:
        try:
            statuses[n] = _provider(n).status()
        except PLUGIN_ERRORS:
            continue
    return _write_entries(statuses, Path(skills_dir))


def _sync_cloud(cloud_st: dict) -> None:
    try:
        client = cloud_status._make_client()
        if client is not None:
            cloud_status._maybe_sync(client, status=cloud_st)
    except PLUGIN_ERRORS:
        pass


def _sync_plugin_entries(statuses: dict) -> None:
    try:
        _write_entries(statuses, cloud_status._claude_home() / "skills")
    except PLUGIN_ERRORS:
        pass


def main(argv: list[str] | None = None) -> int:
    try:
        stdin_text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except OSError:
        stdin_text = ""
    try:
        prev = cloud_status._prev_output(stdin_text)
    except Exception:
        prev = ""
    seg = ""
    try:
        seg, statuses, cloud_st = _compute()
        if cloud_st is not None:
            _sync_cloud(cloud_st)
        _sync_plugin_entries(statuses)
    except Exception:
        seg = ""
    print(" · ".join(x for x in (prev, seg) if x))
    return 0


if __name__ == "__main__":
    sys.exit(main())

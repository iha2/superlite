"""Discard the open prototype: save its report, archive the build's run files, remove the sandbox, mark discarded."""
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import proto  # noqa: E402
import run_archive  # noqa: E402
import session_sandboxes  # noqa: E402
import state  # noqa: E402

KEEP = ("feature.json", "orchestrator.json")


def _writable(func, path, _exc):
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _remove_sandbox(checkout: Path, sb: Path) -> bool:
    """Remove `sb` only when it sits under `<checkout>/.superlite/sandboxes/`; True when removed or already gone."""
    home = (checkout / state.DIR_NAME / "sandboxes").resolve()
    real = Path(sb).resolve()
    if not real.is_relative_to(home) or real == home or ".." in Path(sb).parts:
        print(f"proto: not removing {Path(sb).as_posix()} (outside {home.as_posix()})", file=sys.stderr)
        return False
    if Path(sb).is_symlink():
        Path(sb).unlink()
    elif real.is_dir():
        shutil.rmtree(real, onerror=_writable)
    return True


def _legacy(checkout: Path, st: dict) -> None:
    """A worktree prototype: remove the worktree and delete its branch (an adopted one is Claude's, left alone)."""
    if st.get("adopted"):
        st["branch_kept"] = True
        return
    try:
        if Path(st.get("worktree", "")).is_dir():
            base.git("worktree", "remove", "--force", st["worktree"], cwd=checkout, check=False)
        base.git("worktree", "prune", cwd=checkout, check=False)
        if st.get("branch"):
            base.git("branch", "-D", st["branch"], cwd=checkout, check=False)
    except base.GitError as e:
        print(f"proto: worktree cleanup: {e}", file=sys.stderr)
    st["branch_kept"] = False


def drain_tests(sl: Path) -> bool:
    import run_tests
    live = run_tests.summary_path(sl)
    if run_tests.pending_runs(live) and run_tests.wait_all(live) != 0:
        print("proto: tests still running; retry when they finish", file=sys.stderr)
        return False
    return True


def discard_open(sl: Path, reason: str = "discard") -> dict | None:
    """Discard the open prototype in run folder `sl`; the saved state, or None when none is open."""
    sl = Path(sl)
    checkout = sl.parent
    st = proto.read_state(sl)
    if not st or st["status"] == "discarded":
        return None
    run_sl = proto.run_dir_of(st) or sl
    if not drain_tests(run_sl):
        return {"status": "tests-running"}
    rep = proto.proto_report(st)  # before the run files move
    if rep:
        st["proto_report"] = rep
        proto.write_state(sl, st)
    if st.get("sandbox"):
        rid = st.get("run_id")
        run_archive.archive(sl, run_archive.current_slug(checkout), unfinished=True, plan=st.get("plan"),
                            rid=f"{rid}-unfinished" if rid else None, keep=KEEP)
        if not _remove_sandbox(checkout, Path(st["sandbox"])):
            st["sandbox_kept"] = True
    else:
        _legacy(checkout, st)
    st.update(status="discarded", discarded=proto.now_ms(), discard_reason=reason)
    proto.write_state(sl, st)
    if st.get("sandbox"):
        try:  # a session-row failure never fails the discard
            session_sandboxes.record(checkout, st["topic"], session=st.get("session"),
                                     path=proto.sandbox_rel(st), run_id=st.get("run_id"), status="discarded")
        except (IndexError, ValueError, OSError, TypeError, AttributeError):
            pass
    return st

"""Undo journal: run steps, and on failure undo the finished ones newest first. Stdlib only."""
import sys


class JournalError(Exception):
    """The original error, tagged with the step that failed."""


class Journal:
    def __init__(self):
        self._undos = []
        self._step = ""

    def __enter__(self):
        return self

    def step(self, label: str) -> None:
        """Name the step about to run; a failure is reported against it."""
        self._step = label

    def undo(self, fn, label: str = "") -> None:
        self._undos.append((fn, label))

    def __exit__(self, exc_type, exc, tb):
        if not isinstance(exc, Exception):
            return False  # Ctrl-C, SystemExit: let them through untouched
        for fn, label in reversed(self._undos):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 | deliberate boundary: every remaining undo must run, so nothing is left half-done
                print(f"journal: undo {label or fn!r} failed: {e}", file=sys.stderr)
        step = self._step or (self._undos[-1][1] if self._undos else "")
        if step:
            raise JournalError(f"{step}: {exc}") from exc
        return False

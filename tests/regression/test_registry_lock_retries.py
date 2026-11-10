"""registry_lock retries when Windows msvcrt.locking gives up with EDEADLK (errno 36) while another command holds it."""
import errno
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))

import base  # noqa: E402
import session_registry as R  # noqa: E402


def test_lock_retries_after_edeadlk(tmp_path, monkeypatch):
    real, calls = base._lock, []

    def flaky(fh):
        calls.append(1)
        if len(calls) < 3:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        real(fh)

    monkeypatch.setattr(base, "_lock", flaky)
    with R.registry_lock(tmp_path):
        pass
    assert len(calls) == 3


def test_other_lock_errors_still_raise(tmp_path, monkeypatch):
    def broken(fh):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(base, "_lock", broken)
    with pytest.raises(OSError):
        with R.registry_lock(tmp_path):
            pass

"""Windows: background launches must not make console children pop up windows."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"


def test_no_spawn_site_uses_detached_process():
    # DETACHED_PROCESS makes CREATE_NO_WINDOW ignored, so every console child opens a visible window.
    bad = []
    files = list((ROOT / "skills").rglob("*.py")) + list((ROOT / "bench").glob("*.py"))
    for f in files:
        if f.name == Path(__file__).name:
            continue
        text = chr(10).join(ln.split("#", 1)[0] for ln in f.read_text(encoding="utf-8", errors="replace").splitlines())  # comments may name the flag
        if re.search(r"DETACHED_PROCESS|creationflags[^\n]*0x0*8\b|0x00000008", text):
            bad.append(f.name)
    assert not bad, bad


@pytest.mark.skipif(os.name != "nt", reason="Windows console behaviour")
def test_detached_helper_children_get_no_console_window(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import _pyrun
    out = tmp_path / "w.txt"
    child = (
        "import subprocess, sys;"
        "r = subprocess.run([sys.executable, '-c', "
        "'import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())'], capture_output=True, text=True);"
        f"open(r'{out}', 'w').write(r.stdout.strip())"
    )
    proc = _pyrun.spawn_detached([sys.executable, "-c", child], tmp_path / "log.txt")
    proc.wait(timeout=60)
    for _ in range(100):
        if out.exists() and out.read_text().strip():
            break
        time.sleep(0.1)
    assert out.read_text().strip() == "0"   # a grandchild console window handle of 0 means no window exists

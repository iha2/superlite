import shutil
import subprocess
import sys
from pathlib import Path

PROBES = Path(__file__).resolve().parents[2] / "skills/swarm/probes"
sys.path.insert(0, str(PROBES))
from probe_pool import Cfg  # noqa: E402
import probe_regress  # noqa: E402

ADAPTER = "def call(x):\n    raise ValueError(x)\n"


def make(tmp_path):
    ad = tmp_path / ".superlite/probes/adapter.py"
    ad.parent.mkdir(parents=True)
    ad.write_text(ADAPTER, encoding="utf-8")
    return Cfg(tmp_path, ad, None, 5.0), ad


def gen(cfg):
    group = {"category": "file", "kind": "crash", "key": "k", "size": 9, "probe": "p1"}
    rep = {"status": "fail", "input": "'x'", "id": "p1",
           "probe_meta": {"entry": "call", "module": "/nonexistent.py", "name": "n"}}
    return probe_regress.write_regression(cfg, group, rep, 3)


def test_adapter_copied(tmp_path):
    cfg, ad = make(tmp_path)
    rel = gen(cfg)
    copy = tmp_path / "tests/regression/probe_adapter.py"
    assert copy.read_bytes() == ad.read_bytes()
    text = (tmp_path / rel).read_text(encoding="utf-8")
    assert "ADAPTER = ROOT / 'tests/regression/probe_adapter.py'" in text


def test_after_finish(tmp_path):
    cfg, ad = make(tmp_path)
    rel = gen(cfg)
    shutil.rmtree(tmp_path / ".superlite")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path / rel)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_user_file_kept(tmp_path):
    cfg, ad = make(tmp_path)
    mine = tmp_path / "tests/regression/probe_adapter.py"
    mine.parent.mkdir(parents=True)
    mine.write_text("# mine\n", encoding="utf-8")
    rel = gen(cfg)
    assert mine.read_text(encoding="utf-8") == "# mine\n"
    assert (tmp_path / "tests/regression/probe_adapter_gen.py").read_text(encoding="utf-8") == ADAPTER
    assert "tests/regression/probe_adapter_gen.py" in (tmp_path / rel).read_text(encoding="utf-8")


def test_generated_adapter_revisions_replaced(tmp_path):
    """A copy that starts with review_adapter's header is ours: a third revision overwrites it, no OSError."""
    sys.path.insert(0, str(PROBES.parent / "scripts"))
    import review_adapter
    cfg, ad = make(tmp_path)
    head = review_adapter._ADAPTER_HEAD.replace("__REL_ROOT__", "'.'")
    for rev in range(3):
        src = head + f"\n# revision {rev}\n" + ADAPTER
        ad.write_text(src, encoding="utf-8")
        rel = probe_regress._copy_adapter(cfg, tmp_path / "tests/regression", tmp_path)
        assert (tmp_path / rel).read_text(encoding="utf-8") == src
    assert rel == "tests/regression/probe_adapter.py"

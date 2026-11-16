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


def _adapter_ref(path):
    """The ADAPTER path a generated test names, read by evaluating its assignment, not by matching text."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "ADAPTER")
    return ast.literal_eval(node.value.right)


def test_adapter_copied(tmp_path):
    cfg, ad = make(tmp_path)
    rel = gen(cfg)
    copy = tmp_path / _adapter_ref(tmp_path / rel)
    assert copy.parent == tmp_path / "tests/regression"
    assert copy.read_bytes() == ad.read_bytes()


def test_after_finish(tmp_path):
    cfg, ad = make(tmp_path)
    rel = gen(cfg)
    shutil.rmtree(tmp_path / ".superlite")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path / rel)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_user_file_kept(tmp_path):
    cfg, ad = make(tmp_path)
    for name in ("probe_adapter.py", "probe_adapter_gen.py"):
        mine = tmp_path / "tests/regression" / name
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("# mine\n", encoding="utf-8")
    rel = gen(cfg)
    for name in ("probe_adapter.py", "probe_adapter_gen.py"):
        assert (tmp_path / "tests/regression" / name).read_text(encoding="utf-8") == "# mine\n"
    assert (tmp_path / _adapter_ref(tmp_path / rel)).read_text(encoding="utf-8") == ADAPTER


def test_second_adapter_keeps_earlier_tests_working(tmp_path):
    """Generating for a plan with a different adapter must not change what an earlier test loads."""
    cfg, ad = make(tmp_path)
    rel1 = gen(cfg)
    ref1 = _adapter_ref(tmp_path / rel1)
    ad.write_text("def call(x):\n    return None\n", encoding="utf-8")  # a second plan: call no longer raises
    rel2 = gen(cfg)
    ref2 = _adapter_ref(tmp_path / rel2)
    assert rel1 != rel2 and ref1 != ref2
    assert (tmp_path / ref1).read_text(encoding="utf-8") == ADAPTER
    assert (tmp_path / ref2).read_text(encoding="utf-8") != ADAPTER
    for rel in (rel1, rel2):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path / rel)],
                           capture_output=True, text=True, cwd=tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr


def test_old_named_adapter_still_loads(tmp_path):
    """A test generated before content-addressing names probe_adapter.py: that file stays and still runs."""
    cfg, ad = make(tmp_path)
    rel = gen(cfg)
    reg = tmp_path / "tests/regression"
    old = reg / "probe_adapter.py"
    old.write_bytes(ad.read_bytes())
    text = (tmp_path / rel).read_text(encoding="utf-8").replace(_adapter_ref(tmp_path / rel), "tests/regression/probe_adapter.py")
    legacy = reg / "test_probe_file_9.py"
    legacy.write_text(text, encoding="utf-8")
    assert _adapter_ref(legacy) == "tests/regression/probe_adapter.py"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(legacy)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_same_adapter_one_snapshot(tmp_path):
    cfg, ad = make(tmp_path)
    r1 = probe_regress._copy_adapter(cfg, tmp_path / "tests/regression", tmp_path)
    r2 = probe_regress._copy_adapter(cfg, tmp_path / "tests/regression", tmp_path)
    assert r1 == r2 and len(list((tmp_path / "tests/regression").glob("probe_adapter*.py"))) == 1

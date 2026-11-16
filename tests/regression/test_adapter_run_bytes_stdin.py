"""Generated adapter run() must accept bytes stdin (pass through) and str stdin (encoded)."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/swarm/scripts"
sys.path.insert(0, str(SCRIPTS))
import review_adapter  # noqa: E402


def _adapter(tmp_path):
    src = (review_adapter._ADAPTER_HEAD + review_adapter._ADAPTER_RUN)
    src = src.replace("__REL_ROOT__", repr(".")).replace("__KINDS__", "[]").replace(
        "__ENTRIES__", repr([{"package": "p", "run": "python -c \"import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())\""}]))
    f = tmp_path / "adapter.py"
    f.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_ad_bytes", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bytes_and_str_stdin(tmp_path):
    ad = _adapter(tmp_path)
    rc, out, _ = ad.run([], b"\xff\xfe\x00\x80\xc0\xaf")
    assert rc == 0 and out == "��\x00���"
    rc, out, _ = ad.run([], "hé")
    assert rc == 0 and out == "hé"

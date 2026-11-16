"""Superlite as an importable package. `superlite.learn_signals` is skills/swarm/scripts/learn_signals.py,
mapped in at build time (see pyproject.toml), so the plugin file stays the single source."""
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("superlite")
except PackageNotFoundError:  # not installed
    __version__ = "0+unknown"

# Editable install from a checkout: no built copy exists, so look for the plugin script where it lives.
_scripts = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
if not (Path(__file__).parent / "learn_signals.py").exists() and (_scripts / "learn_signals.py").is_file():
    __path__.append(str(_scripts))

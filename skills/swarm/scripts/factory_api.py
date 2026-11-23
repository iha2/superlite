"""factory_api: error types and the superlite prompt helper."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

from cloud_client import CloudError  # noqa: E402

SUPERLITE_PREAMBLE = ("Build this with superlite: use the superlite skills through your Skill tool, starting with "
                      "superlite:using-superlite and superlite:start (slash commands may not exist here), and set the "
                      "AI orchestrator yourself with `superlite-cli orchestrator set ai`. "
                      "Treat the text below as the plan or request.")


class FactoryError(CloudError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Unsupported(FactoryError):
    def __init__(self, factory: str, verb: str):
        super().__init__(f"{factory}: {verb} is not supported")
        self.factory = factory
        self.verb = verb


def require(provider, verb: str) -> None:
    if verb not in provider.caps:
        raise Unsupported(provider.name, verb)


def superlite_prompt(text: str) -> str:
    return SUPERLITE_PREAMBLE + "\n\n" + text

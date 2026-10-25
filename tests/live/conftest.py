import os
import shutil

import pytest

CLAUDE = shutil.which("claude") or os.environ.get("CLAUDE_BIN")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: one-turn agent instruction tests (costs money)"
    )


@pytest.fixture
def require_claude():
    if not CLAUDE:
        pytest.skip("claude CLI not on PATH")

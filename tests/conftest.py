"""Shared pytest settings for every test folder."""
import os

import pytest

# `-n auto` (pyproject addopts) starts one worker per logical CPU. Most tests spawn git and bash,
# so past a handful of workers they contend for process creation and disk instead of speeding up.
MAX_AUTO_WORKERS = 8


@pytest.hookimpl(optionalhook=True)  # xdist may be absent (run_tests then runs serially)
def pytest_xdist_auto_num_workers(config):
    """Cap `-n auto` at MAX_AUTO_WORKERS; an explicit `-n N` or PYTEST_XDIST_AUTO_NUM_WORKERS still wins."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return min(cpus or 1, MAX_AUTO_WORKERS)

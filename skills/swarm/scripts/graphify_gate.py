#!/usr/bin/env python3
"""Decide whether Graphify should be used for this repo. Prints one JSON object."""
import argparse
import json
import os
import shutil
import subprocess
import sys


def tracked_stats(root):  # O(F + N) + process spawn
    out = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8", "replace")
    files = [f for f in out.split("\0") if f]
    loc = 0
    for f in files:
        try:
            with open(os.path.join(root, f), "rb") as fh:
                loc += sum(1 for _ in fh)
        except OSError:
            pass
    return len(files), loc


def decide(root, min_files, min_loc):  # O(F + N) + process spawn
    try:
        n_files, loc = tracked_stats(root)
    except (subprocess.CalledProcessError, OSError):
        n_files, loc = 0, 0
    installed = shutil.which("graphify") is not None
    allowed_env = "graphify" in [
        p.strip() for p in os.environ.get("SUPERLITE_ALLOW_INSTALL", "").split(",")
    ]
    res = {"use": False, "reason": "", "installed": installed,
           "needs_install": False, "allowed": allowed_env}
    if n_files < min_files and loc < min_loc:
        res["reason"] = "small repo"
    elif installed:
        res.update(use=True, reason="graphify installed")
    else:
        res["needs_install"] = True
        if allowed_env:
            res.update(use=True, reason="install allowed")
        else:
            res["reason"] = "graphify missing, install not allowed"
    return res


def main():  # O(F + N) + process spawn
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--min-files", type=int, default=50)
    ap.add_argument("--min-loc", type=int, default=10000)
    a = ap.parse_args()
    print(json.dumps(decide(a.root, a.min_files, a.min_loc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

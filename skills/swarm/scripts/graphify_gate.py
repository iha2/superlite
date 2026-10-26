#!/usr/bin/env python3
"""Decide whether Graphify should be used for this repo. Prints one JSON object."""
# Complexity notation:
#   F = files tracked by git, N = total bytes of tracked files, P = directories on PATH,
#   e = length of SUPERLITE_ALLOW_INSTALL
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import shutil  # O(1)
import subprocess  # O(1)
import sys  # O(1)


def tracked_stats(root):  # O(F + N) + process spawn
    out = subprocess.run(  # O(F) output + process spawn
        ["git", "-C", root, "ls-files", "-z"],  # O(1)
        capture_output=True, check=True,  # O(1)
    ).stdout.decode("utf-8", "replace")  # O(F)
    files = [f for f in out.split("\0") if f]  # O(F)
    loc = 0  # O(1)
    for f in files:  # O(F) iterations
        try:  # O(1)
            with open(os.path.join(root, f), "rb") as fh:  # O(1) open syscall
                loc += sum(1 for _ in fh)  # O(file bytes) per iteration: reads and counts every line
        except OSError:  # O(1)
            pass  # O(1)
    return len(files), loc  # O(1)


def decide(root, min_files, min_loc):  # O(F + N) + process spawn
    try:  # O(1)
        n_files, loc = tracked_stats(root)  # O(F + N) + process spawn
    except (subprocess.CalledProcessError, OSError):  # O(1)
        n_files, loc = 0, 0  # O(1)
    installed = shutil.which("graphify") is not None  # O(P) PATH search
    allowed_env = "graphify" in [  # O(e)
        p.strip() for p in os.environ.get("SUPERLITE_ALLOW_INSTALL", "").split(",")  # O(e)
    ]  # O(e)
    res = {"use": False, "reason": "", "installed": installed,  # O(1)
           "needs_install": False, "allowed": allowed_env}  # O(1)
    if n_files < min_files and loc < min_loc:  # O(1)
        res["reason"] = "small repo"  # O(1)
    elif installed:  # O(1)
        res.update(use=True, reason="graphify installed")  # O(1)
    else:  # O(1)
        res["needs_install"] = True  # O(1)
        if allowed_env:  # O(1)
            res.update(use=True, reason="install allowed")  # O(1)
        else:  # O(1)
            res["reason"] = "graphify missing, install not allowed"  # O(1)
    return res  # O(1)


def main():  # O(F + N) + process spawn
    ap = argparse.ArgumentParser()  # O(1)
    ap.add_argument("--root", default=".")  # O(1)
    ap.add_argument("--min-files", type=int, default=50)  # O(1)
    ap.add_argument("--min-loc", type=int, default=10000)  # O(1)
    a = ap.parse_args()  # O(1)
    print(json.dumps(decide(a.root, a.min_files, a.min_loc)))  # O(F + N) + process spawn
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)

"""Test double for pt_review.py: appends its argv to .superlite/pt_marker.txt, one line per call.
Seam checks run in parallel and an "a" append is not atomic on Windows, so a mkdir lock serializes them."""
import os
import sys
import time

os.makedirs(".superlite", exist_ok=True)
lock = os.path.join(".superlite", "pt_marker.lock")
for _ in range(600):
    try:
        os.mkdir(lock)
        break
    except FileExistsError:
        time.sleep(0.05)
else:
    sys.exit("fake_pt_review: pt_marker.lock still held after 30 s")
try:
    with open(os.path.join(".superlite", "pt_marker.txt"), "a", encoding="utf-8") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
finally:
    os.rmdir(lock)

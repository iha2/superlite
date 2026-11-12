"""Lessons store and the cache step: admission, grace, eviction, readmission and proven practices.

    lessons_store.hub_dir(cwd)                      the hub run dir holding lessons.json
    lessons_store.load(hub) / update(hub, fn)       read, or locked read-modify-write
    lessons_store.add_signal / note_run / step / control   the store and cache operations on a loaded store

Stdlib only.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import lessons_score  # noqa: E402
import state  # noqa: E402

CAP = 50
GRACE = 3
PROVEN_U = 0.2
PROVEN_N = 5
LINE_CUT = 160


def hub_dir(cwd) -> Path:  # O(1) plus two process calls
    try:
        return state.run_dir(state.hub_root(Path(cwd)))
    except (base.GitError, OSError):
        return state.run_dir(Path(cwd))


def empty() -> dict:  # O(1)
    return {"v": 1, "run_index": [], "modules": {}, "lessons": {}}


def load(hub: Path) -> dict:  # O(n) in file size
    data = base.read_json(Path(hub) / "lessons.json", None)
    if not isinstance(data, dict) or "lessons" not in data:
        return empty()
    for k, v in empty().items():
        data.setdefault(k, v)
    return data


def update(hub: Path, fn) -> dict:  # O(n) in file size
    def apply(raw: dict) -> dict:
        store = raw if isinstance(raw, dict) and "lessons" in raw else empty()
        for k, v in empty().items():
            store.setdefault(k, v)
        out = fn(store)
        return store if out is None else out
    return base.locked_update(Path(hub) / "lessons.json", apply)


def lesson_id(kind: str, module: str, key: str) -> str:  # O(1)
    return "L-" + hashlib.sha1(f"{kind}|{module}|{key}".encode("utf-8")).hexdigest()[:6]


def add_signal(store: dict, kind: str, module: str, key: str, text: str, source: str, run: str,
               verified: bool = False) -> str:  # O(r) in runs
    lid = lesson_id(kind, module, key)
    les = store["lessons"].setdefault(lid, {
        "id": lid, "kind": kind, "module": module, "text": "", "sources": [], "runs": [],
        "verified": False, "status": "candidate", "pinned": False, "proven": False,
        "deliveries": [], "u": 0.0, "grace_from": 0, "admitted_run": None, "baseline": None,
    })
    if source not in les["sources"]:
        les["sources"].append(source)
    les["runs"] = sorted(set(les["runs"]) | {run})
    les["text"] = text[:LINE_CUT]
    les["verified"] = bool(les["verified"] or verified)
    return lid


def note_run(store: dict, run: str) -> None:  # O(r) in runs
    if run not in store["run_index"]:
        store["run_index"].append(run)


def _cached(store: dict) -> list:  # O(n)
    return [x for x in store["lessons"].values() if x["status"] == "cached"]


def _past_grace(les: dict) -> bool:  # O(1)
    return len(les["deliveries"]) - les.get("grace_from", 0) >= GRACE


def _retire(les: dict) -> None:  # O(1)
    les["status"] = "retired"
    les["retired_runs"] = len(les["runs"])
    les["proven"] = False


def step(store: dict) -> list:  # O(n log n) in lessons
    sc = lessons_score
    events: list = []
    ri = store["run_index"]
    lessons = store["lessons"]
    # 1. score cached lessons, evict the useless, flag the proven
    for lid in sorted(lessons):
        les = lessons[lid]
        if les["status"] != "cached":
            continue
        les["u"] = sc.usefulness(les["deliveries"],
                                 les["baseline"] if les["baseline"] is not None else 0.5, ri)
        if _past_grace(les) and les["u"] <= 0 and not les["pinned"]:
            _retire(les)
            events.append(f"evicted {lid} (U {les['u']:.2f})")
            continue
        proven = les["kind"] == "practice" and les["u"] >= PROVEN_U and len(les["deliveries"]) >= PROVEN_N
        if proven and not les["proven"]:
            events.append(f"proven {lid}")
        les["proven"] = proven
    # 2. readmit retired lessons that gained a run (dropped never returns)
    for lid in sorted(lessons):
        les = lessons[lid]
        if les["status"] == "retired" and len(les["runs"]) > les.get("retired_runs", len(les["runs"])):
            les["status"] = "cached"
            les["grace_from"] = len(les["deliveries"])
            les.pop("retired_runs", None)
            events.append(f"readmitted {lid}")
    # 3. admit candidates, displacing the lowest-rank evictable lesson when full
    for lid in sorted(lessons):
        les = lessons[lid]
        if les["status"] != "candidate" or not (len(les["runs"]) >= 2 or les["verified"]):
            continue
        if len(_cached(store)) >= CAP:
            victims = [x for x in _cached(store) if not x["pinned"] and _past_grace(x)]
            if not victims:
                continue
            v = min(victims, key=lambda x: (sc.rank(x), x["id"]))
            _retire(v)
            events.append(f"evicted {v['id']} (U {v['u']:.2f})")
        les["status"] = "cached"
        les["admitted_run"] = ri[-1] if ri else None
        les["baseline"] = sc.baseline(store["modules"], les["module"], les["admitted_run"], ri)
        les["grace_from"] = len(les["deliveries"])
        events.append(f"admitted {lid}")
    return events


def control(store: dict, lid: str, action: str) -> bool:  # O(1)
    les = store["lessons"].get(lid)
    if les is None or action not in ("drop", "pin", "restore"):
        return False
    if action == "drop":
        les["status"] = "dropped"
        les["pinned"] = False
        les["proven"] = False
    elif action == "pin":
        les["pinned"] = True
    else:
        if les["status"] in ("dropped", "retired"):
            les["status"] = "candidate"
            les.pop("retired_runs", None)
        les["pinned"] = False
    return True

#!/usr/bin/env python3
"""Validate docs pages against the real superlite code."""
import argparse
import re
import sys
from pathlib import Path

GUIDE = {"start", "flows", "run", "sessions"}
HEADINGS = ["What you do", "What superlite does", "Example"]
MAX_WORDS = 300


def real_names(main_dir):
    m = Path(main_dir)
    cmd = {p.stem for p in (m / "commands").glob("*.md")}
    tool = set()
    for d in ("skills/swarm/scripts", "skills/swarm/probes"):
        for p in (m / d).glob("*"):
            if p.suffix in (".py", ".sh") and not p.name.startswith("_"):
                tool.add(p.stem)
    skill = {p.name for p in (m / "skills").glob("*") if p.is_dir()}
    skill |= {p.stem for p in (m / "agents").glob("*.md")}
    return {"command": cmd, "tool": tool, "skill": skill}


def _split(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:] if nl != -1 else ""
    return text


def _fences(body):
    """Return (prose_lines, blocks); blocks are (lang, content)."""
    prose, blocks, cur, lang = [], [], None, ""
    for line in body.split("\n"):
        s = line.strip()
        if cur is None:
            if s.startswith("```"):
                cur, lang = [], s[3:].strip()
            else:
                prose.append(line)
        elif s.startswith("```"):
            blocks.append((lang, "\n".join(cur)))
            cur = None
        else:
            cur.append(line)
    return prose, blocks


def _link_ok(docs, target):
    path = re.split(r"[#?]", target, maxsplit=1)[0]
    rel = path[len("/superlite"):].strip("/")
    if not rel:
        return any((docs / f"index{e}").exists() for e in (".md", ".mdx"))
    for e in (".md", ".mdx"):
        if (docs / f"{rel}{e}").exists() or (docs / rel / f"index{e}").exists():
            return True
    return False


def _pages(docs):
    out = []
    for p in sorted(list(docs.rglob("*.md")) + list(docs.rglob("*.mdx"))):
        rel = p.relative_to(docs)
        if rel.stem == "index":
            continue
        out.append((p, rel))
    return out


def check(docs_dir, main_dir):
    docs, main = Path(docs_dir), Path(main_dir)
    names = real_names(main_dir)
    problems = []
    for p, rel in _pages(docs):
        r = rel.as_posix()
        group = rel.parts[0]
        text = p.read_text()
        body = _split(text)
        prose_lines, blocks = _fences(body)
        first = next((l.strip() for l in body.split("\n") if l.strip()), "")
        guide = group in GUIDE
        if guide and not (first.startswith("```mermaid") or first.startswith("```text")):
            problems.append(f"{r}: no diagram first")
        prose = [l for l in prose_lines if not l.strip().startswith("|") and not l.startswith("#")]
        words = len(re.findall(r"\S+", " ".join(prose)))
        if words > MAX_WORDS:
            problems.append(f"{r}: {words} prose words (max {MAX_WORDS})")
        if guide:
            heads = [l[3:].strip() for l in prose_lines if l.startswith("## ")]
            idx = [heads.index(h) if h in heads else -1 for h in HEADINGS]
            if -1 in idx or idx != sorted(idx):
                problems.append(f"{r}: headings must be {', '.join(HEADINGS)} in order")
        for c in sorted(set(re.findall(r"/superlite:([a-z0-9-]+)", text))):
            if c not in names["command"]:
                problems.append(f"{r}: /superlite:{c} not a command")
        for t in sorted(set(re.findall(r"superlite-cli ([a-z0-9_-]+)", text))):
            if t not in names["tool"]:
                problems.append(f"{r}: superlite-cli {t} not a tool")
        for s in sorted(set(re.findall(r"(?<![/\w-])superlite:([a-z0-9-]+)", text))):
            if s not in names["skill"]:
                problems.append(f"{r}: superlite:{s} not a skill or agent")
        for t in sorted(set(re.findall(r"\]\((/superlite(?:/[^)\s]*)?)\)", text))):
            if not _link_ok(docs, t):
                problems.append(f"{r}: link {t} not found")
        if group == "flows":
            flow = main / "assets" / "flows" / f"{p.stem}.txt"
            if flow.exists():
                want = flow.read_text().rstrip("\n")
                got = [c for l, c in blocks if l == "text"]
                if not any(c.rstrip("\n") == want for c in got):
                    problems.append(f"{r}: flow screen differs from assets/flows/{p.stem}.txt")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--main", required=True)
    a = ap.parse_args()
    probs = check(a.docs, a.main)
    if probs:
        print("\n".join(probs))
        return 1
    print(f"check_pages: {len(_pages(Path(a.docs)))} pages ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

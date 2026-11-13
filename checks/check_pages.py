#!/usr/bin/env python3
"""Validate docs pages against the real superlite code."""
import argparse
import os
import re
import sys
from pathlib import Path

GUIDE = {"start", "flows", "run", "sessions"}
MAX_WORDS = 900
TLDR = ":::tip[TL;DR]"
TLDR_WORDS = 60
MERMAID_TYPES = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gitGraph")
SITE = Path(__file__).resolve().parent.parent
# reference tables whose first column holds bare names: page -> real_names key
NAME_TABLES = {"reference/skills.md": "skill_dirs", "reference/agents.md": "agent_files"}


def resolve_main(arg=None):
    """Main checkout: --main, else env SUPERLITE_MAIN, else <site>/_main if it exists, else the parent folder."""
    if arg:
        return Path(arg)
    if os.environ.get("SUPERLITE_MAIN"):
        return Path(os.environ["SUPERLITE_MAIN"])
    if (SITE / "_main").is_dir():
        return SITE / "_main"
    return SITE.parent


def is_main(path):
    return (Path(path) / "commands").is_dir() and (Path(path) / "skills").is_dir()


def real_names(main_dir):
    m = Path(main_dir)
    cmd = {p.stem for p in (m / "commands").glob("*.md")}
    tool = set()
    for d in ("skills/swarm/scripts", "skills/swarm/probes"):
        for p in (m / d).glob("*"):
            if p.suffix in (".py", ".sh") and not p.name.startswith("_"):
                tool.add(p.stem)
    skill_dirs = {p.name for p in (m / "skills").glob("*") if p.is_dir()}
    agent_files = {p.stem for p in (m / "agents").glob("*.md")}
    return {"command": cmd, "tool": tool, "skill": skill_dirs | agent_files,
            "skill_dirs": skill_dirs, "agent_files": agent_files}


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


def _tldr_problem(body):
    """Problem text for the TL;DR aside (first non-blank body line, `import` lines skipped), else None."""
    lines = body.split("\n")
    i = next((n for n, l in enumerate(lines) if l.strip() and not l.startswith("import ")), None)
    if i is None or lines[i].strip() != TLDR:
        return "no :::tip[TL;DR] aside first"
    inner = []
    for l in lines[i + 1:]:
        if l.strip() == ":::":
            break
        inner.append(l)
    else:
        return "TL;DR aside not closed"
    if len(re.findall(r"\S+", " ".join(inner))) > TLDR_WORDS:
        return f"TL;DR over {TLDR_WORDS} words"
    return None


def _diagram_before_heading(body):
    """True when a mermaid or text fence opens before the first `## ` heading outside fences."""
    infence = False
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            if not infence and s[3:].strip().split(" ")[0] in ("mermaid", "text"):
                return True
            infence = not infence
        elif not infence and line.startswith("## "):
            return False
    return False


def _mermaid_type(content):
    for l in content.split("\n"):
        s = l.strip()
        if s and not s.startswith("%%"):
            return s.split()[0]
    return ""


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
    return [(p, p.relative_to(docs)) for p in sorted(list(docs.rglob("*.md")) + list(docs.rglob("*.mdx")))]


def _links(text):
    """Site-absolute targets from Markdown links, `href="..."` attributes and frontmatter/YAML `link:` values."""
    out = set(re.findall(r"\]\((/superlite(?:/[^)\s]*)?)\)", text))
    out |= set(re.findall(r"""\bhref=["'](/superlite(?:/[^"']*)?)["']""", text))
    out |= set(re.findall(r"""^\s*(?:-\s+)?link:\s*["']?(/superlite(?:/[^\s"']*)?)""", text, re.M))
    return sorted(out)


def _table_names(body):
    """Bare names in the first column of Markdown tables, header and separator rows skipped."""
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cell = s.strip("|").split("|", 1)[0].strip()
        if not cell or set(cell) <= set("-: "):
            continue
        m = re.fullmatch(r"`([^`]+)`", cell)
        if m:
            out.append(m.group(1))
    return out


def check(docs_dir, main_dir):
    docs, main = Path(docs_dir), Path(main_dir)
    names = real_names(main_dir)
    problems = []
    for p, rel in _pages(docs):
        r = rel.as_posix()
        group = rel.parts[0]
        index = rel.stem == "index"
        text = p.read_text()
        body = _split(text)
        prose_lines, blocks = _fences(body)
        guide = group in GUIDE and not index
        if not index:
            bad = _tldr_problem(body)
            if bad:
                problems.append(f"{r}: {bad}")
        if guide:
            if not _diagram_before_heading(body):
                problems.append(f"{r}: no diagram before the first ## heading")
            if not any(l.startswith("## Example") for l in prose_lines):
                problems.append(f"{r}: no ## Example section")
        prose = [l for l in prose_lines
                 if not l.strip().startswith("|") and not l.startswith("#")
                 and not l.startswith("import ") and l.strip() != ":::"
                 and not re.match(r"^\s*(\d+\.\s+|[-*]\s+)?!\[", l)]
        words = len(re.findall(r"\S+", " ".join(prose)))
        if words > MAX_WORDS and not index:
            problems.append(f"{r}: {words} prose words (max {MAX_WORDS})")
        for lang, content in blocks:
            if lang == "mermaid":
                t = _mermaid_type(content)
                if t not in MERMAID_TYPES:
                    problems.append(f"{r}: mermaid type {t} not allowed")
        for c in sorted(set(re.findall(r"/superlite:([a-z0-9-]+)", text))):
            if c not in names["command"]:
                problems.append(f"{r}: /superlite:{c} not a command")
        for t in sorted(set(re.findall(r"superlite-cli ([a-z0-9_-]+)", text))):
            if t not in names["tool"]:
                problems.append(f"{r}: superlite-cli {t} not a tool")
        for s in sorted(set(re.findall(r"(?<![/\w-])superlite:([a-z0-9-]+)", text))):
            if s not in names["skill"]:
                problems.append(f"{r}: superlite:{s} not a skill or agent")
        for t in _links(text):
            if not _link_ok(docs, t):
                problems.append(f"{r}: link {t} not found")
        if r in NAME_TABLES:
            key = NAME_TABLES[r]
            for n in _table_names(body):
                if n not in names[key]:
                    problems.append(f"{r}: {n} not {'a skill' if key == 'skill_dirs' else 'an agent'} on main")
        if group == "flows" and not index:
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
    ap.add_argument("--main", help="main checkout; default env SUPERLITE_MAIN, then _main, then ..")
    a = ap.parse_args()
    main_dir = resolve_main(a.main)
    if not is_main(main_dir):
        print(f"check_pages: no main checkout at {main_dir}; pass --main, set SUPERLITE_MAIN, "
              f"or run `git worktree add _main main`", file=sys.stderr)
        return 2
    probs = check(a.docs, main_dir)
    if probs:
        print("\n".join(probs))
        return 1
    print(f"check_pages: {len(_pages(Path(a.docs)))} pages ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

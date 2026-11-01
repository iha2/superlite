"""Every `superlite-cli <tool> ...` call written in the shipped docs names a real tool, subcommand, flag and choice."""
from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
PROBES = ROOT / "skills" / "swarm" / "probes"

DOC_GLOBS = ["skills/**/*.md", "commands/*.md", "agents/*.md", "docs/*.md", "README.md", "AGENTS.md", "assets/*.txt",
              "hooks/*"]
CALL = re.compile(r"superlite-cli[ \t]+([^`\"'|)&;>\n]*)")
PLACEHOLDER = re.compile(r"^(<[^>]*>|\$\{?\w+\}?.*|[A-Z][A-Z0-9_]*|\.\.\.|…|\*|\[.*|.*\].*|\d+(\.\d+)?|Tn|T\d+)$")
OPTIONS = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
BRACES = re.compile(r"\{([^{}]+)\}")


def doc_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for g in DOC_GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p.is_file() and p.suffix not in {".pyc"}:
                seen[p] = None
    return list(seen)


def extract_calls(text: str) -> list[list[str]]:
    """Token lists of every call; prose such as `superlite-cli tool:` has no real tool and is dropped."""
    calls = []
    for m in CALL.finditer(text):
        toks = [t.strip(",.;:\"'()\\") if not t.startswith("-") else t.rstrip(",.;:\"')\\") for t in m.group(1).split()]
        toks = [t.replace("[", "").replace("]", "") for t in toks if t.replace("[", "").replace("]", "")]
        if toks and re.fullmatch(r"[a-z][a-z_0-9]*", toks[0]) and not m.group(1).split()[0].endswith(":"):
            calls.append(toks)
    return calls


def tool_script(tool: str) -> Path | None:
    for d in (SCRIPTS, PROBES):
        if (d / f"{tool}.py").is_file():
            return d / f"{tool}.py"
    return None


@functools.lru_cache(maxsize=None)
def help_of(tool: str, subs: tuple = ()) -> str | None:
    script = tool_script(tool)
    if script is None:
        return None
    env = dict(os.environ, PYTHONIOENCODING="utf-8", COLUMNS="1000", SUPERLITE_NO_AUTO_SESSION="1")
    cp = subprocess.run([sys.executable, str(script), *subs, "--help"], capture_output=True, text=True,
                        encoding="utf-8", env=env, timeout=60, cwd=ROOT)
    return cp.stdout if cp.returncode == 0 else None


def usage_of(text: str) -> str:
    m = re.search(r"usage:(.*?)(\n\s*\n|\Z)", text, re.S)
    return " ".join(m.group(1).split()) if m else ""


def positional_choices(usage: str) -> list[str] | None:
    """The {a,b} set of a positional (subcommand) slot, i.e. one not directly after an option."""
    toks = usage.split()
    for i, t in enumerate(toks):
        if t.startswith("{") and t.endswith("}") and not (i and toks[i - 1].startswith("-")):
            return t[1:-1].split(",")
    return None


def option_choices(usage: str) -> dict[str, list[str]]:
    out = {}
    for m in re.finditer(r"(--[\w-]+)\s+\{([^{}]+)\}", usage):
        out[m.group(1)] = m.group(2).split(",")
    return out


def check_hand_parsed(tool: str, rest: list[str]) -> list[str]:
    """A tool without argparse: every word and flag of the call must appear in its source."""
    src = tool_script(tool).read_text(encoding="utf-8")
    out = []
    for tok in rest:
        if is_placeholder(tok) or tok == "--":
            continue
        word = tok.partition("=")[0]
        if word not in src:
            out.append(f"{tool}: {word!r} appears nowhere in {tool}.py")
    return out


def is_placeholder(tok: str) -> bool:
    return bool(PLACEHOLDER.match(tok))


def check_call(toks: list[str]) -> list[str]:
    """Problems with one call (empty = real)."""
    tool, rest = toks[0], toks[1:]
    if tool_script(tool) is None:
        return [f"unknown tool {tool!r}"]
    problems, subs = [], ()
    text = help_of(tool)
    if text is None:
        return check_hand_parsed(tool, rest)
    i = 0
    while True:  # walk subcommands: each level may declare a positional {a,b} set
        usage = usage_of(text)
        choices = positional_choices(usage)
        nxt = rest[i] if i < len(rest) else None
        if nxt is None or nxt.startswith("-") or is_placeholder(nxt):
            break
        if not choices:  # a hand-routed subcommand (routing miss): real when it has its own --help
            sub_text = help_of(tool, subs + (nxt,))
            if sub_text is None:
                break
            subs, text, i = subs + (nxt,), sub_text, i + 1
            continue
        if nxt not in choices:
            return [f"{tool}{''.join(' ' + s for s in subs)}: {nxt!r} is not one of {choices}"]
        subs += (nxt,)
        sub_text = help_of(tool, subs)
        if sub_text is None:
            return [f"{tool} {' '.join(subs)} --help fails"]
        text, i = sub_text, i + 1
    flags = set(OPTIONS.findall(text))
    opt_ch = option_choices(usage_of(text))
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            name, eq, val = tok.partition("=")
            if name == "--":
                pass
            elif name not in flags:
                problems.append(f"{tool} {' '.join(subs)}: no flag {name}".replace("  ", " "))
            elif name in opt_ch:
                v = val if eq else (rest[i + 1] if i + 1 < len(rest) else "")
                if v and not is_placeholder(v) and not v.startswith("-") and "|" not in v and v not in opt_ch[name]:
                    problems.append(f"{tool} {name}: {v!r} is not one of {opt_ch[name]}")
        i += 1
    return problems


def all_calls() -> list[tuple[str, int, list[str]]]:
    out = []
    for f in doc_files():
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if f.parent.name == "hooks":  # a comment is not a call
            text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for call in extract_calls(text):
            line = next((n for n, ln in enumerate(text.splitlines(), 1) if " ".join(call) in ln), 0)
            out.append((f.relative_to(ROOT).as_posix(), line, call))
    return out


def test_every_cli_call_is_real():
    calls = all_calls()
    assert len(calls) > 50, "the extractor found almost no calls"
    bad = []
    for path, line, call in calls:
        for p in check_call(call):
            bad.append(f"{path}:{line}: superlite-cli {' '.join(call)} -> {p}")
    assert not bad, "\n".join(sorted(set(bad)))


def test_made_up_command_fails():
    assert check_call(["notatool", "--x"])
    assert check_call(["dispatch", "--no-such-flag"])
    assert check_call(["session", "nosuchsub"])
    assert check_call(["session", "ensure", "--untracked", "bogus"])
    assert not check_call(["session", "ensure", "--slug", "<topic>", "--untracked", "stash"])
    assert not check_call(["dispatch", "--batch", "1"])


def test_extractor_reads_calls_out_of_prose():
    calls = extract_calls("run `superlite-cli session close --slug S [--keep-branch]` then `superlite-cli tool: x`")
    assert calls == [["session", "close", "--slug", "S", "--keep-branch"]]

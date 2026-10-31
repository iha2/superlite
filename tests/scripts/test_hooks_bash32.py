"""The hooks run under stock macOS /bin/bash 3.2, so bash 4+ syntax is rejected here.

CI runs on Ubuntu (bash 5), where these constructs work; on a Mac they error or expand to
nothing and the guards silently let everything through. The macOS CI job runs the hook
tests under /bin/bash for the runtime side; this lint catches the syntax on every platform.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(p for p in (ROOT / "hooks").iterdir() if p.is_file() and p.suffix != ".json")
SCRIPTS.append(ROOT / "skills" / "swarm" / "scripts" / "sl")

# (pattern, what to use instead)
BANNED = [
    (r"\$\{[A-Za-z0-9_@*\[\]]+(,,?|\^\^?)\}", "case change: printf '%s' \"$x\" | LC_ALL=C tr '[:upper:]' '[:lower:]'"),
    (r"\$'[^']*\\[uU][0-9A-Fa-f]", "$'\\u' escape: write the UTF-8 bytes, $'\\xe2\\x80\\x99'"),
    (r"\b(declare|local|typeset)\s+-[A-Za-z]*[An]", "associative array / nameref"),
    (r"\b(mapfile|readarray|coproc)\b", "while read loop"),
    (r"\bEPOCHSECONDS\b", "$(date +%s)"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*@[QEPAaKUuL]\}", "parameter transformation"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\[-[0-9]+\]\}", "negative array index"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*:[^}:]*:-[0-9]", "negative substring length"),
    (r"\[\[\s+-v\s", "[[ -v ]]: use [ -n \"${x+set}\" ]"),
    (r"(^|\s)\|&\s|&>>|;;&", "bash 4 redirection / case fallthrough"),
    (r"printf\s+(-v\s+\S+\s+)?['\"]?[^'\"]*%\(", "printf %(...)T: use date"),
    (r"\bshopt\s+-s\s+(globstar|lastpipe|direxpand)\b", "bash 4 shopt"),
    (r"\bwait\s+-n\b", "wait -n"),
]


def offences(text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        code = re.sub(r"(^|\s)#.*$", "", line)  # drop comments; good enough for these scripts
        for pat, hint in BANNED:
            if re.search(pat, code):
                out.append(f"{n}: {line.strip()[:100]}  -> {hint}")
    return out


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_bash4_syntax(script):
    text = script.read_text(encoding="utf-8")
    assert not offences(text), f"{script.name} uses bash 4+ syntax:\n" + "\n".join(offences(text))
    # EPOCHREALTIME is empty in bash 3.2: every use needs a date fallback.
    if "EPOCHREALTIME" in text:
        assert "date +%s" in text, f"{script.name} reads EPOCHREALTIME with no `date +%s` fallback"


@pytest.mark.parametrize("snippet", [
    'lc="${cmd,,}"', 'up="${x^^}"', 'p="/${BASH_REMATCH[1],,}/"', "q=$'\\u2019'", "e=$'\\U1F44D'",
    "declare -A seen", "local -n ref=x", "mapfile -t lines < f", 'echo "${x@Q}"', 'echo "${a[-1]}"',
    'echo "${s:0:-3}"', "[[ -v FOO ]]", "cmd |& tee log", "printf '%(%s)T' -1", "wait -n",
])
def test_lint_catches(snippet):
    assert offences(snippet)


@pytest.mark.parametrize("snippet", [
    'x="${CLAUDE_PROJECT_DIR:-${CURSOR_PROJECT_DIR:-}}"', 'now="${now//[.,]/}"', 'x="${now:0:${#now}-3}"',
    "q=$'\\xe2\\x80\\x99'", "# uses ${var,,} in bash 5", 'r="${p:$((${#projn}+1))}"', "local p rel pl",
])
def test_lint_allows(snippet):
    assert not offences(snippet)

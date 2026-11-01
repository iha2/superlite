"""The one plan reader: tasks, their fields, the task graph, and which edges a consumer must wait on."""
import re
from dataclasses import dataclass, field
from pathlib import Path

TASK_ID = r"T\d+[a-z]*"
HEADING = re.compile(rf"^#{{3,4}}\s+({TASK_ID})\b(.*)$")
ANY_HEADING = re.compile(r"^#{1,4}\s")
FIELD = re.compile(r"^\*\*([^*:]+?)(?:\s*\([^)]*\))?:\*\*\s*(.*)$")
EG = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?e\.g\.(?:\*\*)?:?\s*(.+?)\s*$", re.I)
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
REF_TAIL = rf"\s+\(({TASK_ID}(?:\s*[,/]\s*{TASK_ID})*)(?:\s*[,;:]\s*[^()]*)?\)\s*$"
SIG = re.compile(r"^([A-Za-z_][\w.]*)\s*\(")
CALL = re.compile(r"^`?([A-Za-z_][\w.]*)\s*\(")


class PlanError(Exception):
    pass


def _ticks(s: str) -> str:
    return s.strip().strip("`").strip()


def _braces(s: str) -> list:
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    out = []
    for alt in m.group(1).split(","):
        out.extend(_braces(s[:m.start()] + alt.strip() + s[m.end():]))
    return out


def _split_top(s: str) -> list:
    parts, depth, cur = [], 0, []
    for ch in s:
        depth += (ch in "{([") - (ch in "})]")
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


@dataclass
class Task:
    id: str
    title: str = ""
    fields: dict = field(default_factory=dict)

    def _items(self, name: str) -> list:
        out = []
        for raw in self.fields.get(name, []):
            for part in re.split(r"\s+·\s+", raw.strip()):
                part = _ticks(part.lstrip("-* ").strip())
                if part:
                    out.append(part)
        return out

    @property
    def owns(self) -> list:
        out = []
        for raw in self.fields.get("owns", []):
            raw = re.sub(r"\([^()]*\)", "", raw)  # parenthetical notes
            for part in _split_top(raw):
                out.extend(_owns_paths(part))
        return out

    @property
    def produces(self) -> list:
        return self._items("produces")

    @property
    def consumes(self) -> list:
        return self._items("consumes")

    @property
    def examples(self) -> list:
        return [_ticks(x) for x in self.fields.get("e.g.", []) if x.strip()]


def _owns_paths(part: str) -> list:
    """The paths one comma-separated Owns entry names (brace alternatives expanded)."""
    part = _ticks(part.lstrip("-* ").strip()).rstrip(".;").strip()
    paths = (p.strip().replace("\\", "/") for p in (_braces(part) if part else []))
    return [p for p in paths if p]


def signature(item: str):
    """(key, name) of a `name(args) -> ret` interface, or None. key is `Class.member` or `member`."""
    s = re.sub(REF_TAIL, "", _ticks(item))
    m = SIG.match(s)
    if not m:
        return None
    segs = m.group(1).split(".")
    if len(segs) >= 2 and segs[-2][:1].isupper():
        return f"{segs[-2]}.{segs[-1]}", m.group(1)
    if segs[-1][:1].isupper():
        return f"{segs[-1]}.__init__", m.group(1)
    return segs[-1], m.group(1)


def _example_names(task: Task) -> set:
    names = set()
    for ex in task.examples:
        m = CALL.match(_ticks(ex))
        if m:
            names.update((m.group(1), m.group(1).rsplit(".", 1)[-1]))
    return names


class Plan:
    def __init__(self, text: str, path: Path = None):
        self.path = path
        self.lines = text.replace("\r\n", "\n").split("\n")
        self.tasks: dict = {}
        self.edges: list = []
        fenced = self._parse_tasks()
        self._parse_edges(fenced)

    def _parse_tasks(self) -> list:
        """Fill self.tasks; return the fence blocks as (info, content lines)."""
        cur, cur_field, fence, blocks = None, None, None, []
        for ln in self.lines:
            m = FENCE.match(ln)
            if fence is not None:
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                    blocks.append((fence[2], fence[3]))
                    fence = None
                else:
                    fence[3].append(ln)
                continue
            if m:
                fence = (m.group(1)[0], len(m.group(1)), m.group(2).strip(), [])
                continue
            cur, cur_field = self._line(ln, cur, cur_field)
        if fence is not None:
            blocks.append((fence[2], fence[3]))
        return blocks

    def _line(self, ln, cur, cur_field):
        h = HEADING.match(ln)
        if h:
            title = re.split(r"\s+·\s+|\s{2,}", h.group(2).strip().lstrip("·").strip())[0].strip()
            cur = self.tasks.setdefault(h.group(1), Task(h.group(1), title))
            return cur, None
        if ANY_HEADING.match(ln):
            return None, None
        if cur is None:
            return cur, cur_field
        eg = EG.match(ln)
        if eg:
            cur.fields.setdefault("e.g.", []).append(eg.group(1).strip())
            return cur, "e.g."
        fm = FIELD.match(ln.strip())
        if fm:
            name = fm.group(1).strip().lower()
            cur.fields.setdefault(name, []).append(fm.group(2).strip())
            return cur, name
        if not ln.strip():
            return cur, None
        if cur_field and (re.match(r"^\s*[-*]\s+", ln) or ln.startswith((" ", "\t"))):
            cur.fields[cur_field].append(ln.strip())
            return cur, cur_field
        return cur, None

    def _parse_edges(self, blocks: list) -> None:
        for info, content in blocks:
            if not info.lower().startswith("mermaid"):
                continue
            idx = next((i for i, ln in enumerate(content) if re.match(r"^\s*%%\s*taskgraph\b", ln)), None)
            if idx is not None:
                self._graph_lines(content[idx + 1:])

    def _graph_lines(self, lines: list) -> None:
        for raw in lines:
            ln = raw.strip()
            if ln and not ln.startswith("%%"):
                self._graph_line(ln)

    def _graph_line(self, ln: str) -> None:
        groups = [re.findall(rf"\b{TASK_ID}\b", re.sub(r'\[[^\]]*\]|\([^)]*\)|"[^"]*"', "", p))
                  for p in re.split(r"\s*-->\s*(?:\|[^|]*\|\s*)?", ln)]
        for left, right in zip(groups, groups[1:]):
            for a, b in ((a, b) for a in left for b in right):
                if (a, b) not in self.edges:
                    self.edges.append((a, b))

    def _consumed_pairs(self) -> dict:
        """(producer, consumer) -> [has_example per consumed interface]."""
        produced = {}
        for t in self.tasks.values():
            for item in t.produces:
                sig = signature(item)
                if sig:
                    produced.setdefault(sig[0], []).append((t.id, sig[1]))
        pairs = {}
        for t in self.tasks.values():
            for item in t.consumes:
                self._pair_item(t, item, produced, pairs)
        return pairs

    def _pair_item(self, consumer, item, produced, pairs) -> None:
        sig = signature(item)
        if not sig:
            return
        ref = re.search(REF_TAIL, _ticks(item))
        refs = re.findall(TASK_ID, ref.group(1)) if ref else []
        for pid, name in produced.get(sig[0], []):
            if refs and pid not in refs:
                continue
            names = _example_names(self.tasks[pid])
            ok = name in names or name.rsplit(".", 1)[-1] in names
            pairs.setdefault((pid, consumer.id), []).append(ok)

    def waited_edges(self) -> list:
        """Edges a consumer must wait on: drop Ta -> Tb when every interface Tb consumes from Ta has an e.g. line."""
        pairs = self._consumed_pairs()
        return [e for e in self.edges if not (pairs.get(e) and all(pairs[e]))]


def read(path: str) -> Plan:
    p = Path(path)
    if not p.is_file():
        raise PlanError(f"plan not found: {path}")
    return Plan(p.read_bytes().decode("utf-8-sig"), p)

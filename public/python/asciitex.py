from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import inf
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from typing import Protocol, runtime_checkable


# ============================================================
# Extension hooks
# ============================================================

@runtime_checkable
class ParserExtension(Protocol):
    def try_parse(
        self,
        *,
        parser: "TexLikeParser",
        lines: List[str],
        i: int,
        pending_label: Optional[str],
    ) -> Tuple[Optional["Node"], int, Optional[str]]:
        """Try to parse a custom node starting at lines[i].

        Return (node, new_i, new_pending_label) if handled, else (None, i, pending_label).
        """


@runtime_checkable
class NumberingExtension(Protocol):
    def try_number(
        self,
        *,
        node: "Node",
        meta: Dict[str, Any],
        counters: "Counters",
        refs: "ReferenceResolver",
    ) -> bool:
        """Handle numbering/label registration for node. Return True if handled."""


@runtime_checkable
class RenderExtension(Protocol):
    def try_render(
        self,
        *,
        node: "Node",
        meta: Dict[str, Any],
        compiler: "TexLikeMonospaceCompiler",
        max_width: int,
    ) -> Optional[Union["Box", "FloatItem"]]:
        """Render a node to a Box or FloatItem. Return None if not handled."""


@dataclass
class ExtensionRegistry:
    parser: List[ParserExtension] = field(default_factory=list)
    numbering: List[NumberingExtension] = field(default_factory=list)
    render: List[RenderExtension] = field(default_factory=list)

    def add(self, ext: Any) -> None:
        """Register an extension object that may implement one or more extension protocols."""
        if isinstance(ext, ParserExtension):
            self.parser.append(ext)
        if isinstance(ext, NumberingExtension):
            self.numbering.append(ext)
        if isinstance(ext, RenderExtension):
            self.render.append(ext)


# ============================================================
# Dimension evaluation (\textwidth, \columnwidth, \textheight)
# ============================================================

_DIM_TOKEN_RE = re.compile(
    r"^\s*(?P<num>[+-]?(?:\d+(?:\.\d+)?|\.\d+))?\s*(?P<var>\\[A-Za-z]+)?\s*$"
)

@dataclass
class DimContext:
    textwidth: int
    textheight: int
    columnwidth: int
    canvaswidth: int
    canvasheight: int  # in auto-height mode this is a soft/initial height


def eval_dim(expr: Union[str, int, float, None], ctx: DimContext, *, default: int) -> int:
    r"""Evaluate a TeX-ish dimension expression into an integer character count.

    Supported:
      - integers: 40
      - variables: \textwidth \columnwidth \textheight \canvaswidth \canvasheight
      - scaled variables: .2\columnwidth 0.5\textwidth 2\columnwidth
      - plain var: \textwidth
    """
    if expr is None:
        return int(default)
    if isinstance(expr, int):
        return int(expr)
    if isinstance(expr, float):
        return int(round(expr))

    s = str(expr).strip()
    if not s:
        return int(default)

    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)

    m = _DIM_TOKEN_RE.match(s)
    if not m:
        return int(default)

    num_s = m.group("num")
    var_s = m.group("var")

    mult = float(num_s) if num_s is not None else 1.0
    base: Optional[float] = None
    if var_s:
        v = var_s.lower()
        if v == "\\textwidth":
            base = float(ctx.textwidth)
        elif v == "\\columnwidth":
            base = float(ctx.columnwidth)
        elif v == "\\textheight":
            base = float(ctx.textheight)
        elif v == "\\columnheight":
            base = float(ctx.textheight)
        elif v == "\\canvaswidth":
            base = float(ctx.canvaswidth)
        elif v == "\\canvasheight":
            base = float(ctx.canvasheight)
        else:
            base = None
    else:
        base = 1.0

    if base is None:
        return int(default)
    return max(0, int(round(mult * base)))


# ============================================================
# Canvas and layout primitives (auto-growing height supported)
# ============================================================

@dataclass
class Canvas:
    width: int
    height: int
    fill: str = " "
    grid: List[List[str]] = field(init=False)

    def __post_init__(self) -> None:
        self.grid = [[self.fill for _ in range(self.width)] for _ in range(self.height)]

    def ensure_height(self, h: int) -> None:
        if h <= self.height:
            return
        for _ in range(h - self.height):
            self.grid.append([self.fill for _ in range(self.width)])
        self.height = h

    def draw_text(self, x: int, y: int, text: str) -> None:
        if y < 0:
            return
        if y >= self.height:
            self.ensure_height(y + 1)
        for i, ch in enumerate(text):
            xx = x + i
            if 0 <= xx < self.width and 0 <= y < self.height:
                self.grid[y][xx] = ch

    def blit(self, x: int, y: int, block_lines: List[str]) -> None:
        for dy, line in enumerate(block_lines):
            self.draw_text(x, y + dy, line)

    def to_string(self) -> str:
        return "\n".join("".join(row) for row in self.grid)


@dataclass
class Box:
    lines: List[str]
    width: int
    height: int

    @staticmethod
    def from_lines(lines: List[str], width: Optional[int] = None) -> "Box":
        w = width if width is not None else (max((len(l) for l in lines), default=0))
        padded = [(l[:w]).ljust(w) for l in lines]
        return Box(lines=padded, width=w, height=len(padded))


@dataclass
class PlacedBox:
    box: Box
    x: int
    y: int
    kind: str = "block"
    meta: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# TeX-style hyphenation WITHOUT external packages
# ============================================================

def _letters_only(s: str) -> str:
    return s.lower()

def _parse_pattern(pat: str) -> Tuple[str, List[int]]:
    pat = pat.strip()
    if not pat or pat.startswith("%"):
        return "", []
    key_chars: List[str] = []
    points: List[int] = [0]
    for ch in pat:
        if ch.isdigit():
            points[-1] = int(ch)
        else:
            key_chars.append(ch)
            points.append(0)
    return "".join(key_chars), points

class TeXHyphenator:
    def __init__(self, leftmin: int = 2, rightmin: int = 2):
        self.leftmin = leftmin
        self.rightmin = rightmin
        self.trie: Dict[str, dict] = {}
        self._loaded = False

    def load_patterns_text(self, text: str) -> None:
        for raw in text.split():
            if raw.startswith("%"):
                continue
            key, pts = _parse_pattern(_letters_only(raw))
            if not key:
                continue
            node = self.trie
            for c in key:
                node = node.setdefault(c, {})
            node["_"] = pts
        self._loaded = True

    def hyphen_positions(self, word: str) -> List[int]:
        if not self._loaded:
            return []
        w = _letters_only(word)
        if len(w) < (self.leftmin + self.rightmin + 1):
            return []

        s = "." + w + "."
        scores = [0] * (len(w) + 1)

        for i in range(len(s)):
            node = self.trie
            j = i
            while j < len(s) and s[j] in node:
                node = node[s[j]]
                if "_" in node:
                    pts: List[int] = node["_"]
                    start_in_word = i - 1
                    for k, val in enumerate(pts):
                        pos = start_in_word + k
                        if 0 <= pos <= len(w):
                            if val > scores[pos]:
                                scores[pos] = val
                j += 1

        positions: List[int] = []
        for pos in range(self.leftmin, len(w) - self.rightmin + 1):
            if scores[pos] % 2 == 1:
                positions.append(pos)
        return positions


# ============================================================
# Knuth–Plass line breaking (variable widths) + segmenting
# ============================================================

@dataclass(frozen=True)
class Segment:
    text: str
    word_id: int
    part_index: int
    is_last_part: bool

@dataclass(frozen=True)
class Boundary:
    is_word_boundary: bool
    can_break: bool
    penalty: int
    inserts_hyphen: bool

def _is_wordlike(tok: str) -> bool:
    return any(c.isalnum() for c in tok)

def text_to_segments(
    paragraph: str,
    hyphenator: Optional[TeXHyphenator],
    hyphen_penalty: int = 50
) -> Tuple[List[Segment], List[Boundary]]:
    tokens = paragraph.split()
    segments: List[Segment] = []
    boundaries: List[Boundary] = [Boundary(True, False, 0, False)]  # dummy for index 0

    wid = 0
    for tok in tokens:
        if hyphenator is None or not _is_wordlike(tok):
            parts = [tok]
            hyph_pos: List[int] = []
        else:
            hyph_pos = hyphenator.hyphen_positions(tok)
            if not hyph_pos:
                parts = [tok]
            else:
                parts = []
                start = 0
                for p in hyph_pos:
                    parts.append(tok[start:p])
                    start = p
                parts.append(tok[start:])

        for pi, part in enumerate(parts):
            segments.append(Segment(part, wid, pi, pi == len(parts) - 1))
            if len(segments) == 1:
                continue

            prev = segments[-2]
            cur = segments[-1]
            if prev.word_id != cur.word_id:
                boundaries.append(Boundary(True, True, 0, False))              # between words
            else:
                boundaries.append(Boundary(False, True, hyphen_penalty, True))  # inside word

        wid += 1

    return segments, boundaries


@dataclass
class KPConfig:
    tolerance: float = 4.0
    fitness_penalty: int = 100
    line_penalty: int = 10
    hyphen_penalty: int = 50

def _fitness_class(ratio: float) -> int:
    if ratio < -0.5:
        return 0
    if ratio < 0.5:
        return 1
    if ratio < 1.0:
        return 2
    return 3

@dataclass
class BreakChoice:
    prev_j: int
    prev_line: int
    cost: float
    fit: int
    ratio: float

def _line_base_len(
    segments: List[Segment],
    boundaries: List[Boundary],
    i: int,
    j: int,
    break_inserts_hyphen: bool
) -> Tuple[int, int]:
    if i >= j:
        return 0, 0
    base_len = len(segments[i].text)
    gaps = 0
    for k in range(i + 1, j):
        b = boundaries[k]
        if b.is_word_boundary:
            base_len += 1
            gaps += 1
        base_len += len(segments[k].text)
    if break_inserts_hyphen:
        base_len += 1
    return base_len, gaps

def _badness_and_fit(
    base_len: int,
    gaps: int,
    line_width: int,
    cfg: KPConfig,
    is_last_line: bool
) -> Tuple[Optional[int], Optional[float], Optional[int]]:
    if base_len > line_width:
        return None, None, None
    if is_last_line:
        return cfg.line_penalty, 0.0, 1
    if gaps <= 0:
        short = line_width - base_len
        b = (short * 100) ** 3 if short > 0 else 0
        return cfg.line_penalty + int(b), float(short), 1
    diff = line_width - base_len
    ratio = diff / gaps
    if ratio > cfg.tolerance:
        return None, None, None
    b = int((abs(ratio) * 100) ** 3)
    return cfg.line_penalty + b, float(ratio), _fitness_class(ratio)

def knuth_plass_variable_width(
    segments: List[Segment],
    boundaries: List[Boundary],
    line_widths: List[int],
    cfg: KPConfig,
) -> Tuple[List[int], List[int]]:
    n = len(segments)
    if n == 0:
        return [0], [0]

    Lmax = len(line_widths)
    dp = [[inf] * (Lmax + 1) for _ in range(n + 1)]
    prev: List[List[Optional[BreakChoice]]] = [[None] * (Lmax + 1) for _ in range(n + 1)]
    prev_fit = [[1] * (Lmax + 1) for _ in range(n + 1)]

    dp[0][0] = 0.0

    for l in range(1, Lmax + 1):
        width = line_widths[l - 1]
        is_last_possible = (l == Lmax)

        for j in range(1, n + 1):
            for i in range(0, j):
                if dp[i][l - 1] == inf:
                    continue

                if j != n:
                    bnd = boundaries[j]
                    if not bnd.can_break:
                        continue
                    break_pen = bnd.penalty
                    inserts_hyphen = bnd.inserts_hyphen
                else:
                    break_pen = 0
                    inserts_hyphen = False

                base_len, gaps = _line_base_len(segments, boundaries, i, j, inserts_hyphen)
                b, ratio, fit = _badness_and_fit(
                    base_len, gaps, width, cfg,
                    is_last_line=(j == n) or is_last_possible
                )
                if b is None:
                    continue

                fp = 0
                pf = prev_fit[i][l - 1]
                if fit is not None and abs(pf - fit) > 1:
                    fp = cfg.fitness_penalty

                cand = dp[i][l - 1] + (b + break_pen) + fp
                if cand < dp[j][l]:
                    dp[j][l] = cand
                    prev_fit[j][l] = fit if fit is not None else 1
                    prev[j][l] = BreakChoice(i, l - 1, cand, prev_fit[j][l], ratio or 0.0)

    best_l = None
    best_cost = inf
    for l in range(1, Lmax + 1):
        if dp[n][l] < best_cost:
            best_cost = dp[n][l]
            best_l = l

    if best_l is None:
        # greedy fallback (rare with reasonable Lmax)
        breaks = [0]
        line_no = [0]
        i = 0
        l = 0
        while i < n and l < Lmax:
            width = line_widths[l]
            j = i + 1
            best_j = j
            while j <= n:
                inserts = (j != n and boundaries[j].inserts_hyphen)
                base_len, _ = _line_base_len(segments, boundaries, i, j, inserts)
                if base_len <= width:
                    best_j = j
                    j += 1
                else:
                    break
            if best_j <= i:
                best_j = i + 1
            breaks.append(best_j)
            line_no.append(l + 1)
            i = best_j
            l += 1
        if breaks[-1] != n:
            breaks.append(n)
            line_no.append(l)
        return breaks, line_no

    # backtrack
    breaks: List[int] = [n]
    lines: List[int] = [best_l]
    j = n
    l = best_l
    while not (j == 0 and l == 0):
        bc = prev[j][l]
        if bc is None:
            break
        j2, l2 = bc.prev_j, bc.prev_line
        breaks.append(j2)
        lines.append(l2)
        j, l = j2, l2

    breaks.reverse()
    lines.reverse()
    return breaks, lines

def justify_line_from_segments(
    segments: List[Segment],
    boundaries: List[Boundary],
    i: int,
    j: int,
    width: int,
    is_last: bool
) -> str:
    if i >= j:
        return ""

    inserts_hyphen = (j != len(segments) and boundaries[j].inserts_hyphen)
    pieces: List[str] = []
    gap_positions: List[int] = []

    pieces.append(segments[i].text)
    for k in range(i + 1, j):
        b = boundaries[k]
        if b.is_word_boundary:
            gap_positions.append(len(pieces))
            pieces.append(" ")
        pieces.append(segments[k].text)
    if inserts_hyphen:
        pieces.append("-")

    s = "".join(pieces)
    if is_last or not gap_positions:
        return s

    diff = width - len(s)
    if diff <= 0:
        return s

    gaps = len(gap_positions)
    extra_each = diff // gaps
    rem = diff % gaps
    for idx, pos in enumerate(gap_positions):
        add = extra_each + (1 if idx < rem else 0)
        pieces[pos] = " " * (1 + add)
    return "".join(pieces)

def layout_paragraph_into_shape(
    paragraph: str,
    line_widths: List[int],
    cfg: KPConfig,
    hyphenator: Optional[TeXHyphenator]
) -> List[str]:
    segments, boundaries = text_to_segments(paragraph, hyphenator, cfg.hyphen_penalty)
    breaks, _ = knuth_plass_variable_width(segments, boundaries, line_widths, cfg)

    lines: List[str] = []
    for li in range(len(breaks) - 1):
        i, j = breaks[li], breaks[li + 1]
        is_last = (j == len(segments)) or (li == len(line_widths) - 1)
        width = line_widths[li]
        lines.append(justify_line_from_segments(segments, boundaries, i, j, width, is_last))
    return lines


# ============================================================
# Typesetter adapter (Knuth–Plass + hyphenation patterns)
# ============================================================

class TypesetterAdapter:
    """Monospace typesetter with TeX/Liang hyphenation + Knuth–Plass line breaking."""

    def __init__(self) -> None:
        self.hyphenator: Optional[TeXHyphenator] = None
        self.kp = KPConfig(tolerance=4.0, fitness_penalty=100, line_penalty=10, hyphen_penalty=50)

    def load_hyphenation_patterns_text(self, text: str) -> None:
        h = TeXHyphenator()
        h.load_patterns_text(text)
        self.hyphenator = h

    def set_kp_config(
        self,
        *,
        tolerance: float = 4.0,
        fitness_penalty: int = 100,
        line_penalty: int = 10,
        hyphen_penalty: int = 50,
    ) -> None:
        self.kp = KPConfig(
            tolerance=tolerance,
            fitness_penalty=fitness_penalty,
            line_penalty=line_penalty,
            hyphen_penalty=hyphen_penalty,
        )

    def text(self, content: str, max_width: int) -> Box:
        """Paragraph typesetting with KP.

        - Splits into paragraphs on blank lines.
        - Uses constant-width shape: [max_width] * N, where N is estimated.
        """
        # TeX-style dash conventions: em dash first so its three hyphens are not
        # partially consumed by the en-dash replacement.
        content = content.replace("---", "—").replace("--", "–")
        # normalize line endings
        raw_lines = content.splitlines()

        paras: List[str] = []
        buf: List[str] = []
        def flush_para() -> None:
            if buf:
                paras.append(" ".join(" ".join(x.split()) for x in buf).strip())
                buf.clear()

        for ln in raw_lines:
            if ln.strip() == "":
                flush_para()
                paras.append("")  # paragraph break
            else:
                buf.append(ln)
        flush_para()

        out_lines: List[str] = []
        for p in paras:
            if p == "":
                # keep blank line (avoid leading multiple empties)
                if out_lines and out_lines[-1] != "":
                    out_lines.append("")
                elif not out_lines:
                    out_lines.append("")
                continue

            # Estimate a safe upper bound of needed lines so KP can search.
            # (KP is O(n^2 * L). Keep L reasonable but big enough.)
            approx = max(1, len(p) // max(1, max_width - 5))
            max_lines = min(400, approx + 50)  # cap to avoid blow-ups on huge paragraphs
            line_widths = [max_width] * max_lines
            lines = layout_paragraph_into_shape(p, line_widths, self.kp, self.hyphenator)

            # trim any accidental empty tail
            while lines and lines[-1] == "":
                lines.pop()

            out_lines.extend(lines)
            out_lines.append("")

        if out_lines and out_lines[-1] == "":
            out_lines.pop()

        return Box.from_lines(out_lines, width=max_width)

    def section(self, level: int, title: str, number: Optional[str], max_width: int) -> Box:
        prefix = f"{number} " if number else ""
        if level == 1:
            line = (prefix + title).upper()
            underline = "─" * min(max_width, len(line))
            return Box.from_lines([line[:max_width], underline.ljust(max_width)], width=max_width)
        line = prefix + title
        return Box.from_lines([line[:max_width].ljust(max_width)], width=max_width)

    def equation(self, latex: str, number: Optional[int], max_width: int) -> Box:
        tag = f"({number})" if number is not None else ""
        inner = latex.strip()
        line = inner
        if tag and len(line) + 1 + len(tag) <= max_width:
            line = line + " " + (" " * (max_width - len(line) - 1 - len(tag))) + tag
        top = "┌" + "─" * (max_width - 2) + "┐"
        mid = "│" + line[: (max_width - 2)].ljust(max_width - 2) + "│"
        bot = "└" + "─" * (max_width - 2) + "┘"
        return Box.from_lines([top, mid, bot], width=max_width)

    def codeblock(self, code: str, max_width: int) -> Box:
        lines = code.rstrip("\n").splitlines() or [""]
        lines = [l[: (max_width - 4)] for l in lines]
        top = "┌" + "─" * (max_width - 2) + "┐"
        mid = ["│ " + l.ljust(max_width - 4) + " │" for l in lines]
        bot = "└" + "─" * (max_width - 2) + "┘"
        return Box.from_lines([top] + mid + [bot], width=max_width)

    def image(self, path: str, width: int, height: int, number: Optional[int]) -> Box:
        label = f"Figure {number}: {path}" if number is not None else f"{path}"
        w = max(4, width)
        h = max(3, height)
        top = "┌" + "─" * (w - 2) + "┐"
        bot = "└" + "─" * (w - 2) + "┘"
        mid: List[str] = []
        inner_w = w - 2
        for i in range(h - 2):
            s = label[:inner_w].ljust(inner_w) if i == 0 else "".ljust(inner_w)
            mid.append("│" + s + "│")
        return Box.from_lines([top] + mid + [bot], width=w)

    def diagram(self, spec: str, width: int, height: int, number: Optional[int]) -> Box:
        label = f"Diagram {number}" if number is not None else "Diagram"
        w = max(4, width)
        h = max(3, height)
        top = "┌" + "─" * (w - 2) + "┐"
        bot = "└" + "─" * (w - 2) + "┘"
        inner_w = w - 2
        lines = [top]
        for i in range(h - 2):
            text = label if i == 0 else (spec.strip().replace("\n", " ")[:inner_w] if i == 1 else "")
            lines.append("│" + text[:inner_w].ljust(inner_w) + "│")
        lines.append(bot)
        return Box.from_lines(lines, width=w)

    def bibliography(self, entries: List[str], max_width: int) -> Box:
        title = "REFERENCES"
        underline = "─" * len(title)
        lines = [title, underline]
        for e in entries:
            lines.extend(self.text(e, max_width).lines)
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
        return Box.from_lines(lines, width=max_width)


# ============================================================
# AST nodes
# ============================================================

DimExpr = Union[int, float, str]

@dataclass
class Node:
    pass

@dataclass
class TextNode(Node):
    text: str

@dataclass
class SectionNode(Node):
    level: int
    title: str
    label: Optional[str] = None

@dataclass
class EquationNode(Node):
    latex: str
    label: Optional[str] = None

@dataclass
class CodeNode(Node):
    code: str

@dataclass
class ImageNode(Node):
    path: str
    width: DimExpr
    height: DimExpr
    label: Optional[str] = None
    placement: str = "t"

@dataclass
class DiagramNode(Node):
    spec: str
    width: DimExpr
    height: DimExpr
    label: Optional[str] = None
    placement: str = "t"

@dataclass
class TwoColumnNode(Node):
    textwidth: DimExpr
    gutter: DimExpr
    balance: bool
    children: List[Node]

@dataclass
class ColumnBreakNode(Node):
    pass

@dataclass
class FloatBarrierNode(Node):
    pass

@dataclass
class BibNode(Node):
    bibfiles: List[str]


# ============================================================
# Parser
# ============================================================

class TexLikeParser:
    def __init__(self, registry: Optional[ExtensionRegistry] = None) -> None:
        self.registry = registry or ExtensionRegistry()

    @staticmethod
    def parse_kv_opts(opt: Optional[str]) -> Dict[str, str]:
        if not opt:
            return {}
        s = opt.strip()[1:-1].strip()
        if not s:
            return {}
        out: Dict[str, str] = {}
        for part in s.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    _cmd_section = re.compile(r"^\\(section|subsection|subsubsection)\{(.*)\}\s*$")
    _cmd_label = re.compile(r"^\\label\{([^}]+)\}\s*$")
    _cmd_bib = re.compile(r"^\\bibliography\{([^}]+)\}\s*$")
    _cmd_columnbreak = re.compile(r"^\\columnbreak\s*$")
    _cmd_floatbarrier = re.compile(r"^\\floatbarrier\s*$")
    _cmd_includeimage = re.compile(r"^\\includeimage(\[[^\]]*\])?\{([^}]+)\}\s*$")
    _begin_env = re.compile(r"^\\begin\{([a-zA-Z]+)\}\s*$")
    _end_env = re.compile(r"^\\end\{([a-zA-Z]+)\}\s*$")
    _begin_diag = re.compile(r"^\\begindiagram(\[[^\]]*\])?\s*$")
    _end_diag = re.compile(r"^\\enddiagram\s*$")
    _begin_twocol = re.compile(r"^\\begin\{twocolumns\}(\[[^\]]*\])?\s*$")
    _end_twocol = re.compile(r"^\\end\{twocolumns\}\s*$")

    def parse(self, src: str) -> List[Node]:
        lines = src.splitlines()
        i = 0
        nodes: List[Node] = []
        pending_label: Optional[str] = None

        def flush_text(buf: List[str]) -> None:
            if buf:
                text = "\n".join(buf).strip("\n")
                if text.strip():
                    nodes.append(TextNode(text=text))
                buf.clear()

        text_buf: List[str] = []

        while i < len(lines):
            line = lines[i]

            matched = False
            for ext in self.registry.parser:
                node, new_i, pending_label = ext.try_parse(
                    parser=self, lines=lines, i=i, pending_label=pending_label
                )
                if node is not None:
                    flush_text(text_buf)
                    nodes.append(node)
                    i = new_i
                    matched = True
                    break
            if matched:
                continue

            m = self._begin_twocol.match(line.strip())
            if m:
                flush_text(text_buf)
                opts = self.parse_kv_opts(m.group(1))
                textwidth: DimExpr = opts.get("textwidth", "\\textwidth")
                gutter: DimExpr = opts.get("gutter", "4")
                balance = opts.get("balance", "true").lower() in ("1", "true", "yes", "on")
                i += 1
                inner_lines: List[str] = []
                depth = 1
                while i < len(lines):
                    if self._begin_twocol.match(lines[i].strip()):
                        depth += 1
                    if self._end_twocol.match(lines[i].strip()):
                        depth -= 1
                        if depth == 0:
                            break
                    inner_lines.append(lines[i])
                    i += 1
                inner_nodes = self.parse("\n".join(inner_lines))
                nodes.append(TwoColumnNode(textwidth=textwidth, gutter=gutter, balance=balance, children=inner_nodes))
                i += 1
                continue

            m = self._begin_env.match(line.strip())
            if m:
                env = m.group(1).lower()
                if env in ("equation", "code"):
                    flush_text(text_buf)
                    i += 1
                    body: List[str] = []
                    while i < len(lines) and not self._end_env.match(lines[i].strip()):
                        body.append(lines[i])
                        i += 1
                    if i < len(lines):
                        i += 1
                    if env == "equation":
                        n = EquationNode(latex="\n".join(body).strip(), label=None)
                        if pending_label:
                            n.label = pending_label
                            pending_label = None
                        nodes.append(n)
                    else:
                        nodes.append(CodeNode(code="\n".join(body)))
                    continue

            m = self._begin_diag.match(line.strip())
            if m:
                flush_text(text_buf)
                opts = self.parse_kv_opts(m.group(1))
                width: DimExpr = opts.get("width", "40")
                height: DimExpr = opts.get("height", "8")
                placement = opts.get("place", "t")
                i += 1
                body: List[str] = []
                while i < len(lines) and not self._end_diag.match(lines[i].strip()):
                    body.append(lines[i])
                    i += 1
                if i < len(lines):
                    i += 1
                n = DiagramNode(spec="\n".join(body), width=width, height=height, placement=placement)
                if pending_label:
                    n.label = pending_label
                    pending_label = None
                nodes.append(n)
                continue

            m = self._cmd_section.match(line.strip())
            if m:
                flush_text(text_buf)
                kind, title = m.group(1), m.group(2).strip()
                level = {"section": 1, "subsection": 2, "subsubsection": 3}[kind]
                n = SectionNode(level=level, title=title)
                if pending_label:
                    n.label = pending_label
                    pending_label = None
                nodes.append(n)
                i += 1
                continue

            m = self._cmd_label.match(line.strip())
            if m:
                flush_text(text_buf)
                pending_label = m.group(1).strip()
                i += 1
                continue

            m = self._cmd_bib.match(line.strip())
            if m:
                flush_text(text_buf)
                files = [p.strip() for p in m.group(1).split(",") if p.strip()]
                nodes.append(BibNode(bibfiles=files))
                i += 1
                continue

            if self._cmd_columnbreak.match(line.strip()):
                flush_text(text_buf)
                nodes.append(ColumnBreakNode())
                i += 1
                continue

            if self._cmd_floatbarrier.match(line.strip()):
                flush_text(text_buf)
                nodes.append(FloatBarrierNode())
                i += 1
                continue

            m = self._cmd_includeimage.match(line.strip())
            if m:
                flush_text(text_buf)
                opts = self.parse_kv_opts(m.group(1))
                path = m.group(2).strip()
                width: DimExpr = opts.get("width", "40")
                height: DimExpr = opts.get("height", "10")
                placement = opts.get("place", "t")
                n = ImageNode(path=path, width=width, height=height, placement=placement)
                if pending_label:
                    n.label = pending_label
                    pending_label = None
                nodes.append(n)
                i += 1
                continue

            text_buf.append(line)
            i += 1

        flush_text(text_buf)
        return nodes


# ============================================================
# Numbering and refs
# ============================================================

@dataclass
class Counters:
    sec: List[int] = field(default_factory=lambda: [0, 0, 0])
    fig: int = 0
    eq: int = 0
    dia: int = 0

    def next_section(self, level: int) -> str:
        idx = level - 1
        self.sec[idx] += 1
        for j in range(idx + 1, len(self.sec)):
            self.sec[j] = 0
        parts = [str(self.sec[k]) for k in range(level) if self.sec[k] > 0]
        return ".".join(parts)

    def next_figure(self) -> int:
        self.fig += 1
        return self.fig

    def next_equation(self) -> int:
        self.eq += 1
        return self.eq

    def next_diagram(self) -> int:
        self.dia += 1
        return self.dia


class ReferenceResolver:
    _ref_pat = re.compile(r"\\ref\{([^}]+)\}")

    def __init__(self) -> None:
        self.label_map: Dict[str, str] = {}

    def register(self, label: str, ref_text: str) -> None:
        self.label_map[label] = ref_text

    def resolve_text(self, s: str) -> str:
        def repl(m: re.Match) -> str:
            key = m.group(1)
            return self.label_map.get(key, f"??({key})")
        return self._ref_pat.sub(repl, s)


# ============================================================
# Floats
# ============================================================

@dataclass
class FloatItem:
    box: Box
    placement: str
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ColumnBreakItem:
    pass

@dataclass
class FloatBarrierItem:
    pass

class FloatQueue:
    def __init__(self) -> None:
        self.top: List[FloatItem] = []
        self.bottom: List[FloatItem] = []
        self.here: List[FloatItem] = []

    def add(self, item: FloatItem) -> None:
        p = (item.placement or "t").lower()
        if p.startswith("b"):
            self.bottom.append(item)
        elif p.startswith("h"):
            self.here.append(item)
        else:
            self.top.append(item)

    def pop_top_that_fits(self, remaining_height: int) -> Optional[FloatItem]:
        for idx, it in enumerate(self.top):
            if it.box.height <= remaining_height:
                return self.top.pop(idx)
        return None

    def pop_bottom_any(self) -> Optional[FloatItem]:
        return self.bottom.pop(0) if self.bottom else None

    def pop_here_all(self) -> List[FloatItem]:
        items = self.here
        self.here = []
        return items



# ============================================================
# Citations (\cite{key1,key2}) + BibTeX
# ============================================================

_CITE_RE = re.compile(r"""\\cite\{([^}]+)\}""")

def _extract_cite_keys(text: str) -> List[str]:
    keys: List[str] = []
    for m in _CITE_RE.finditer(text):
        inner = m.group(1)
        for k in inner.split(","):
            kk = k.strip()
            if kk:
                keys.append(kk)
    return keys

def _replace_cites(text: str, cite_numbers: "OrderedDict[str, int]") -> str:
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        nums: List[str] = []
        for k in [x.strip() for x in inner.split(",") if x.strip()]:
            if k not in cite_numbers:
                cite_numbers[k] = len(cite_numbers) + 1
            nums.append(str(cite_numbers[k]))
        return "[" + ",".join(nums) + "]"
    return _CITE_RE.sub(repl, text)


# ============================================================
# Layout
# ============================================================

@dataclass
class LayoutCursor:
    x: int
    y: int
    region_width: int
    region_height: int

    def remaining_height(self) -> int:
        return self.region_height - self.y

class LayoutEngine:
    def __init__(self, canvas: Canvas) -> None:
        self.canvas = canvas
        self.placed: List[PlacedBox] = []

    def _place_box(self, box: Box, x: int, y: int, kind: str, meta: Dict[str, Any]) -> None:
        self.canvas.blit(x, y, box.lines)
        self.placed.append(PlacedBox(box=box, x=x, y=y, kind=kind, meta=meta))

    @staticmethod
    def _box_role(box: Box) -> str:
        return str(getattr(box, "_role", "block"))

    @classmethod
    def _gap_after_box(cls, box: Box, default_gap: int) -> int:
        role = cls._box_role(box)
        if role == "section":
            return 0
        return default_gap

    def layout_flow(
        self,
        boxes: List[Union[Box, FloatItem, ColumnBreakItem, FloatBarrierItem]],
        cursor: LayoutCursor,
        float_queue: Optional[FloatQueue] = None,
        line_gap: int = 1,
        *,
        auto_height: bool = False,
    ) -> LayoutCursor:
        fq = float_queue or FloatQueue()

        def flush_top(require_text_lines: int = 0) -> None:
            while True:
                it = fq.pop_top_that_fits(cursor.remaining_height())
                if not it:
                    break
                remaining_after = cursor.remaining_height() - it.box.height - line_gap
                if (not auto_height) and require_text_lines > 0 and remaining_after < require_text_lines:
                    fq.top.insert(0, it)
                    break
                self._place_box(it.box, cursor.x, cursor.y, kind="float", meta=it.meta)
                cursor.y += it.box.height + line_gap

        def flush_barrier() -> None:
            for h in fq.pop_here_all():
                if auto_height or h.box.height <= cursor.remaining_height():
                    self._place_box(h.box, cursor.x, cursor.y, kind="float", meta=h.meta)
                    cursor.y += h.box.height + line_gap
            flush_top()
            if auto_height:
                while True:
                    it = fq.pop_bottom_any()
                    if not it:
                        break
                    self._place_box(it.box, cursor.x, cursor.y, kind="float", meta=it.meta)
                    cursor.y += it.box.height + line_gap
                return

            remaining = cursor.remaining_height()
            fits: List[FloatItem] = []
            idx = 0
            while idx < len(fq.bottom):
                it = fq.bottom[idx]
                if it.box.height + line_gap <= remaining:
                    fits.append(fq.bottom.pop(idx))
                    remaining -= (it.box.height + line_gap)
                else:
                    idx += 1

            y_bottom = cursor.region_height - 1
            for it in fits:
                y_bottom -= it.box.height
                if y_bottom < cursor.y:
                    break
                self._place_box(it.box, cursor.x, y_bottom, kind="float", meta=it.meta)
                y_bottom -= line_gap

        flush_top(require_text_lines=2)

        for item in boxes:
            if isinstance(item, ColumnBreakItem):
                continue

            if isinstance(item, FloatBarrierItem):
                flush_barrier()
                continue

            if isinstance(item, FloatItem):
                fq.add(item)
                for h in fq.pop_here_all():
                    if auto_height or h.box.height <= cursor.remaining_height():
                        self._place_box(h.box, cursor.x, cursor.y, kind="float", meta=h.meta)
                        cursor.y += h.box.height + line_gap
                flush_top(require_text_lines=2)
                continue

            box = item
            if (not auto_height) and box.height > cursor.remaining_height():
                break
            self._place_box(box, cursor.x, cursor.y, kind="block", meta={})
            cursor.y += box.height + self._gap_after_box(box, line_gap)
            flush_top(require_text_lines=2)

        if auto_height:
            flush_barrier()
            return cursor

        flush_barrier()
        return cursor

    def layout_two_columns(
            self,
            items: List[Union[Box, FloatItem, ColumnBreakItem, FloatBarrierItem]],
            cursor: LayoutCursor,
            col_width: int,
            gutter: int,
            balance: bool,
            line_gap: int = 1,
            *,
            auto_height: bool = False,
        ) -> LayoutCursor:
            """Two-column layout for a single ordered stream of items.

            Adds three pragmatic controls on top of the existing balancing logic:
              - section headings keep at least two following text lines in the same column
              - top floats are deferred when they would leave only one text line
              - manual \\columnbreak / \\floatbarrier markers can override the automatics
            """
            x0, y0 = cursor.x, cursor.y
            region_h = cursor.region_height - y0

            lc = LayoutCursor(x=x0, y=y0, region_width=col_width, region_height=y0 + region_h)
            rc = LayoutCursor(x=x0 + col_width + gutter, y=y0, region_width=col_width, region_height=y0 + region_h)

            fqL, fqR = FloatQueue(), FloatQueue()
            resB_L = 0
            resB_R = 0

            est_total = 0
            for it in items:
                if isinstance(it, FloatItem):
                    est_total += it.box.height + line_gap
                elif isinstance(it, Box):
                    est_total += it.height + line_gap
            target_h = max(0, est_total // 2)
            switched = False

            def eff_y(c: LayoutCursor, resB: int) -> int:
                return c.y + resB

            def remaining_for(c: LayoutCursor, resB: int) -> int:
                return (c.region_height - c.y) - resB

            def gap_after(box: Box) -> int:
                return self._gap_after_box(box, line_gap)

            def choose_text_column() -> Tuple[LayoutCursor, FloatQueue, str]:
                nonlocal switched
                if not balance:
                    if lc.y < lc.region_height:
                        return lc, fqL, "L"
                    return rc, fqR, "R"

                if (not switched) and ((eff_y(lc, resB_L) - y0) >= target_h) and (lc.y > y0):
                    switched = True
                if lc.y >= lc.region_height:
                    switched = True
                return (rc, fqR, "R") if switched else (lc, fqL, "L")

            def choose_float_column(prefer: Optional[str] = None) -> Tuple[LayoutCursor, FloatQueue, str]:
                if prefer == "L":
                    return lc, fqL, "L"
                if prefer == "R":
                    return rc, fqR, "R"
                if not balance:
                    return (rc, fqR, "R") if switched else (lc, fqL, "L")
                if eff_y(lc, resB_L) <= eff_y(rc, resB_R):
                    return lc, fqL, "L"
                return rc, fqR, "R"

            def place_top_floats(c: LayoutCursor, fq: FloatQueue, resB: int, *, require_text_lines: int = 0) -> None:
                while True:
                    it = fq.pop_top_that_fits(remaining_for(c, resB))
                    if not it:
                        break
                    remaining_after = remaining_for(c, resB) - it.box.height - line_gap
                    if (not auto_height) and require_text_lines > 0 and remaining_after < require_text_lines:
                        fq.top.insert(0, it)
                        break
                    self._place_box(it.box, c.x, c.y, kind="float", meta=it.meta)
                    c.y += it.box.height + line_gap

            def flush_column_barrier(c: LayoutCursor, fq: FloatQueue, tag: str) -> None:
                nonlocal resB_L, resB_R
                for h in fq.pop_here_all():
                    if auto_height or h.box.height <= remaining_for(c, resB_L if tag == "L" else resB_R):
                        self._place_box(h.box, c.x, c.y, kind="float", meta=h.meta)
                        c.y += h.box.height + line_gap
                place_top_floats(c, fq, resB_L if tag == "L" else resB_R)
                if auto_height:
                    while True:
                        it = fq.pop_bottom_any()
                        if not it:
                            break
                        self._place_box(it.box, c.x, c.y, kind="float", meta=it.meta)
                        c.y += it.box.height + line_gap
                else:
                    remaining = remaining_for(c, 0)
                    fits: List[FloatItem] = []
                    idx = 0
                    while idx < len(fq.bottom):
                        it = fq.bottom[idx]
                        if it.box.height + line_gap <= remaining:
                            fits.append(fq.bottom.pop(idx))
                            remaining -= (it.box.height + line_gap)
                        else:
                            idx += 1
                    y_bottom = c.region_height - 1
                    for it in fits:
                        y_bottom -= it.box.height
                        if y_bottom < c.y:
                            break
                        self._place_box(it.box, c.x, y_bottom, kind="float", meta=it.meta)
                        y_bottom -= line_gap
                    used = sum(it.box.height + line_gap for it in fits)
                    if tag == "L":
                        resB_L = max(0, resB_L - used)
                    else:
                        resB_R = max(0, resB_R - used)

            def handle_float(item: FloatItem, c: LayoutCursor, fq: FloatQueue, col_tag: str) -> None:
                nonlocal resB_L, resB_R
                p = (item.placement or "t").lower()

                def cur_resB(tag: str) -> int:
                    return resB_L if tag == "L" else resB_R

                def add_resB(tag: str, dh: int) -> None:
                    nonlocal resB_L, resB_R
                    if tag == "L":
                        resB_L += dh
                    else:
                        resB_R += dh

                if p.startswith("b"):
                    fq.bottom.append(item)
                    add_resB(col_tag, item.box.height + line_gap)
                    return

                if p.startswith("h"):
                    if auto_height or item.box.height <= remaining_for(c, cur_resB(col_tag)):
                        remaining_after = remaining_for(c, cur_resB(col_tag)) - item.box.height - line_gap
                        if auto_height or remaining_after >= 2:
                            self._place_box(item.box, c.x, c.y, kind="float", meta=item.meta)
                            c.y += item.box.height + line_gap
                            return

                    if balance:
                        other_c, other_fq, other_tag = (rc, fqR, "R") if col_tag == "L" else (lc, fqL, "L")
                        other_resB = cur_resB(other_tag)
                        place_top_floats(other_c, other_fq, other_resB, require_text_lines=2)
                        if auto_height or item.box.height <= remaining_for(other_c, other_resB):
                            remaining_after = remaining_for(other_c, other_resB) - item.box.height - line_gap
                            if auto_height or remaining_after >= 2:
                                self._place_box(item.box, other_c.x, other_c.y, kind="float", meta=item.meta)
                                other_c.y += item.box.height + line_gap
                                return

                    fq.top.append(item)
                    return

                fq.top.append(item)

            def next_text_box(start_idx: int) -> Optional[Box]:
                for j in range(start_idx, len(items)):
                    cand = items[j]
                    if isinstance(cand, Box):
                        return cand
                    if isinstance(cand, ColumnBreakItem):
                        return None
                return None

            def place_box(box: Box, c: LayoutCursor, resB: int) -> bool:
                if (not auto_height) and box.height > remaining_for(c, resB):
                    return False
                self._place_box(box, c.x, c.y, kind="block", meta={})
                c.y += box.height + gap_after(box)
                return True

            place_top_floats(lc, fqL, resB_L, require_text_lines=2)
            place_top_floats(rc, fqR, resB_R, require_text_lines=2)

            idx = 0
            while idx < len(items):
                item = items[idx]

                if isinstance(item, FloatBarrierItem):
                    flush_column_barrier(lc, fqL, "L")
                    flush_column_barrier(rc, fqR, "R")
                    idx += 1
                    continue

                if isinstance(item, ColumnBreakItem):
                    flush_column_barrier(lc, fqL, "L")
                    switched = True
                    idx += 1
                    continue

                c, fq, tag = choose_text_column()
                resB = resB_L if tag == "L" else resB_R

                place_top_floats(c, fq, resB, require_text_lines=2)
                if balance:
                    place_top_floats(lc, fqL, resB_L, require_text_lines=2)
                    place_top_floats(rc, fqR, resB_R, require_text_lines=2)

                if isinstance(item, FloatItem):
                    p = (item.placement or "t").lower()
                    prefer = tag if p.startswith("h") else None
                    cf, fqf, tagf = choose_float_column(prefer=prefer)
                    resBf = resB_L if tagf == "L" else resB_R
                    place_top_floats(cf, fqf, resBf, require_text_lines=2)
                    handle_float(item, cf, fqf, tagf)
                    place_top_floats(lc, fqL, resB_L, require_text_lines=2)
                    place_top_floats(rc, fqR, resB_R, require_text_lines=2)
                    idx += 1
                    continue

                if self._box_role(item) == "section":
                    nxt = next_text_box(idx + 1)
                    keep_lines = 2 if nxt is None else min(2, nxt.height)
                    need = item.height + gap_after(item) + keep_lines
                    if (not auto_height) and remaining_for(c, resB) < need:
                        if tag == "L":
                            switched = True
                            c, fq, tag = choose_text_column()
                            resB = resB_L if tag == "L" else resB_R
                            place_top_floats(c, fq, resB, require_text_lines=2)

                ok = place_box(item, c, resB)
                if not ok and balance:
                    c2, fq2, tag2 = (rc, fqR, "R") if tag == "L" else (lc, fqL, "L")
                    resB2 = resB_R if tag2 == "R" else resB_L
                    place_top_floats(c2, fq2, resB2, require_text_lines=2)
                    ok2 = place_box(item, c2, resB2)
                    if ok2 and tag == "L" and tag2 == "R":
                        switched = True
                    elif not ok2 and auto_height:
                        self._place_box(item, c.x, c.y, kind="block", meta={})
                        c.y += item.height + gap_after(item)
                idx += 1

            flush_column_barrier(lc, fqL, "L")
            flush_column_barrier(rc, fqR, "R")
            cursor.y = max(lc.y, rc.y)
            return cursor

def _parse_bibtex_files(bibfiles: List[str]) -> Dict[str, Dict[str, str]]:
    """Very small BibTeX parser (good enough for common .bib files).

    Returns: key -> {field: value}
    Notes:
      - Supports @article/@inproceedings/@book/... entries
      - Handles values in braces {...} or quotes "..."
      - Ignores @string, @preamble, @comment
    """
    db: Dict[str, Dict[str, str]] = {}

    entry_re = re.compile(r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE)

    def strip_outer(v: str) -> str:
        v = v.strip().rstrip(",").strip()
        if len(v) >= 2 and ((v[0] == "{" and v[-1] == "}") or (v[0] == '"' and v[-1] == '"')):
            v = v[1:-1].strip()
        return v

    def split_fields(body: str) -> List[str]:
        parts: List[str] = []
        buf: List[str] = []
        depth = 0
        in_quote = False
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == '"' and (i == 0 or body[i-1] != '\\'):
                in_quote = not in_quote
                buf.append(ch)
                i += 1
                continue
            if not in_quote:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth = max(0, depth - 1)
                elif ch == ',' and depth == 0:
                    part = ''.join(buf).strip()
                    if part:
                        parts.append(part)
                    buf = []
                    i += 1
                    continue
            buf.append(ch)
            i += 1
        tail = ''.join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    def clean_tex(s: str) -> str:
        # Very lightweight TeX cleanup for plaintext monospace output.
        s = re.sub(r"\\[a-zA-Z]+\s*\{([^}]*)\}", r"\1", s)  # \emph{X} -> X
        s = s.replace("~", " ")
        s = s.replace("\\&", "&")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    for bf in bibfiles:
        try:
            raw = open(bf, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        i = 0
        while i < len(raw):
            at = raw.find("@", i)
            if at < 0:
                break
            m = entry_re.match(raw, at)
            if not m:
                i = at + 1
                continue
            etype = m.group("type").lower()
            key = m.group("key").strip()
            if etype in ("string", "preamble", "comment"):
                i = m.end()
                continue

            # Find matching closing brace for the whole entry
            j = m.end()
            depth = 1
            in_quote = False
            while j < len(raw) and depth > 0:
                ch = raw[j]
                if ch == '"' and raw[j-1] != '\\':
                    in_quote = not in_quote
                if not in_quote:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                j += 1

            body = raw[m.end(): j-1].strip()
            fields: Dict[str, str] = {}
            for part in split_fields(body):
                if '=' not in part:
                    continue
                k, v = part.split('=', 1)
                k = k.strip().lower()
                v = clean_tex(strip_outer(v))
                if v:
                    fields[k] = v
            fields["_type"] = etype
            db[key] = fields
            i = j
    return db


def _format_bib_entry_plain(num: int, key: str, fields: Dict[str, str]) -> str:
    # Minimal, consistent formatting (unsrt-like numeric)
    authors = fields.get("author") or fields.get("editor") or ""
    title = fields.get("title") or ""
    year = fields.get("year") or ""
    journal = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
    volume = fields.get("volume") or ""
    number = fields.get("number") or ""
    # BibTeX page ranges use ``--``; Unicode output uses an en dash.
    pages = re.sub(r"\s*--+\s*", "–", fields.get("pages") or "")

    parts: List[str] = []
    if authors:
        parts.append(authors)
    if title:
        parts.append(f"{title}.")
    if journal:
        j = journal
        if volume:
            j += f" {volume}"
        if number:
            j += f"({number})"
        if pages:
            j += f", {pages}"
        if year:
            j += f" ({year})"
        parts.append(j + ".")
    elif year:
        parts.append(f"({year}).")
    core = " ".join(p for p in parts if p).strip()
    if not core:
        core = f"{key}"
    return f"[{num}] {core}"


def load_bib_entries(
    bibfiles: List[str],
    citations: Optional["OrderedDict[str, int]"] = None,
    *,
    style: str = "unsrt",
) -> List[str]:
    """Load and format bibliography entries.

    - If citations is provided, entries are returned in first-citation order and numbered accordingly.
    - If citations is None/empty, entries are returned in file order with sequential numbering.
    """
    db = _parse_bibtex_files(bibfiles)

    if citations:
        keys_sorted = sorted(citations.keys(), key=lambda k: citations[k])
        out: List[str] = []
        for k in keys_sorted:
            n = citations[k]
            fields = db.get(k)
            if not fields:
                out.append(f"[{n}] MISSING BIB ENTRY: {k}")
            else:
                out.append(_format_bib_entry_plain(n, k, fields))
        return out

    # No citations collected: dump all entries
    out = []
    for i, (k, fields) in enumerate(db.items()):
        out.append(_format_bib_entry_plain(i + 1, k, fields))
    return out


# ============================================================
# Compiler
# ============================================================

class TexLikeMonospaceCompiler:
    def __init__(
        self,
        typesetter: Optional[TypesetterAdapter] = None,
        registry: Optional[ExtensionRegistry] = None,
    ) -> None:
        self.registry = registry or ExtensionRegistry()
        self.parser = TexLikeParser(registry=self.registry)
        self.typesetter = typesetter or TypesetterAdapter()
        self.counters = Counters()
        self.refs = ReferenceResolver()
        self.cite_numbers: "OrderedDict[str, int]" = OrderedDict()

    def compile(
        self,
        src: str,
        canvas_width: int = 100,
        canvas_height: Optional[int] = 60,
        margin_left: int = 2,
        margin_top: int = 1,
        margin_right: int = 2,
        margin_bottom: int = 1,
        line_gap: int = 1,
    ) -> str:
        nodes = self.parser.parse(src)

        auto_height = canvas_height is None
        initial_height = (margin_top + margin_bottom + 10) if auto_height else int(canvas_height)
        canvas = Canvas(canvas_width, initial_height)
        engine = LayoutEngine(canvas)

        inner_width = canvas_width - margin_left - margin_right
        inner_height = (10**9) if auto_height else (int(canvas_height) - margin_top - margin_bottom)

        numbered_meta: List[Tuple[Node, Dict[str, Any]]] = []
        for n in nodes:
            meta: Dict[str, Any] = {}
            handled = False
            # Collect citation keys in first-appearance order (including inside twocolumns)
            if isinstance(n, TextNode):
                for k in _extract_cite_keys(n.text):
                    if k not in self.cite_numbers:
                        self.cite_numbers[k] = len(self.cite_numbers) + 1
            elif isinstance(n, SectionNode):
                for k in _extract_cite_keys(n.title):
                    if k not in self.cite_numbers:
                        self.cite_numbers[k] = len(self.cite_numbers) + 1
            elif isinstance(n, TwoColumnNode):
                for ch in n.children:
                    if isinstance(ch, TextNode):
                        for k in _extract_cite_keys(ch.text):
                            if k not in self.cite_numbers:
                                self.cite_numbers[k] = len(self.cite_numbers) + 1
                    elif isinstance(ch, SectionNode):
                        for k in _extract_cite_keys(ch.title):
                            if k not in self.cite_numbers:
                                self.cite_numbers[k] = len(self.cite_numbers) + 1

                # Number items inside twocolumns in *global parse order* so numbering stays monotone
                # across the whole document (and labels resolve correctly).
                for ch in n.children:
                    ch_meta: Dict[str, Any] = {}
                    ch_handled = False
                    for ext in self.registry.numbering:
                        if ext.try_number(node=ch, meta=ch_meta, counters=self.counters, refs=self.refs):
                            ch_handled = True
                            break

                    if (not ch_handled) and isinstance(ch, SectionNode):
                        secno = self.counters.next_section(ch.level)
                        ch_meta["secno"] = secno
                        if ch.label:
                            self.refs.register(ch.label, secno)

                    elif (not ch_handled) and isinstance(ch, EquationNode):
                        eqno = self.counters.next_equation()
                        ch_meta["eqno"] = eqno
                        if ch.label:
                            self.refs.register(ch.label, str(eqno))

                    elif (not ch_handled) and isinstance(ch, ImageNode):
                        fno = self.counters.next_figure()
                        ch_meta["figno"] = fno
                        if ch.label:
                            self.refs.register(ch.label, str(fno))

                    elif (not ch_handled) and isinstance(ch, DiagramNode):
                        dno = self.counters.next_diagram()
                        ch_meta["diano"] = dno
                        if ch.label:
                            self.refs.register(ch.label, str(dno))

                    # Attach meta to the node so the renderer can use it later.
                    if ch_meta:
                        setattr(ch, "_meta", ch_meta)

            for ext in self.registry.numbering:
                if ext.try_number(node=n, meta=meta, counters=self.counters, refs=self.refs):
                    handled = True
                    break

            if (not handled) and isinstance(n, SectionNode):
                secno = self.counters.next_section(n.level)
                meta["secno"] = secno
                if n.label:
                    self.refs.register(n.label, secno)

            elif (not handled) and isinstance(n, EquationNode):
                eqno = self.counters.next_equation()
                meta["eqno"] = eqno
                if n.label:
                    self.refs.register(n.label, str(eqno))

            elif (not handled) and isinstance(n, ImageNode):
                fno = self.counters.next_figure()
                meta["figno"] = fno
                if n.label:
                    self.refs.register(n.label, str(fno))

            elif (not handled) and isinstance(n, DiagramNode):
                dno = self.counters.next_diagram()
                meta["diano"] = dno
                if n.label:
                    self.refs.register(n.label, str(dno))

            numbered_meta.append((n, meta))

        resolved_nodes: List[Tuple[Node, Dict[str, Any]]] = []
        for n, meta in numbered_meta:
            if isinstance(n, TextNode):
                resolved_text = self.refs.resolve_text(n.text)
                resolved_text = _replace_cites(resolved_text, self.cite_numbers)
                resolved_nodes.append((TextNode(text=resolved_text), meta))
            elif isinstance(n, SectionNode):
                resolved_title = self.refs.resolve_text(n.title)
                resolved_title = _replace_cites(resolved_title, self.cite_numbers)
                resolved_nodes.append((SectionNode(level=n.level, title=resolved_title, label=n.label), meta))
            else:
                resolved_nodes.append((n, meta))

        cursor = LayoutCursor(
            x=margin_left,
            y=margin_top,
            region_width=inner_width,
            region_height=margin_top + inner_height,
        )

        def ctx_for(col_w: int) -> DimContext:
            return DimContext(
                textwidth=inner_width,
                textheight=inner_height,
                columnwidth=col_w,
                canvaswidth=canvas_width,
                canvasheight=initial_height,
            )

        base_ctx = ctx_for(inner_width)

        render_items: List[Union[Box, FloatItem]] = []

        for n, meta in resolved_nodes:
            rendered = None
            for ext in self.registry.render:
                rendered = ext.try_render(node=n, meta=meta, compiler=self, max_width=inner_width)
                if rendered is not None:
                    break
            if rendered is not None:
                render_items.append(rendered)
                continue

            if isinstance(n, TextNode):
                box = self.typesetter.text(n.text, max_width=inner_width)
                box._role = "text"
                render_items.append(box)

            elif isinstance(n, SectionNode):
                secno = meta.get("secno")
                box = self.typesetter.section(n.level, n.title, number=secno, max_width=inner_width)
                box._role = "section"
                render_items.append(box)

            elif isinstance(n, EquationNode):
                eqno = meta.get("eqno")
                render_items.append(self.typesetter.equation(n.latex, number=eqno, max_width=inner_width))

            elif isinstance(n, CodeNode):
                render_items.append(self.typesetter.codeblock(n.code, max_width=inner_width))

            elif isinstance(n, ColumnBreakNode):
                render_items.append(ColumnBreakItem())

            elif isinstance(n, FloatBarrierNode):
                render_items.append(FloatBarrierItem())

            elif isinstance(n, ImageNode):
                fno = meta.get("figno")
                w = eval_dim(n.width, base_ctx, default=min(40, inner_width))
                h = eval_dim(n.height, base_ctx, default=10)
                box = self.typesetter.image(n.path, width=min(w, inner_width), height=h, number=fno)
                render_items.append(FloatItem(box=box, placement=n.placement, meta={"kind": "image", "number": fno, "path": n.path}))

            elif isinstance(n, DiagramNode):
                dno = meta.get("diano")
                w = eval_dim(n.width, base_ctx, default=min(40, inner_width))
                h = eval_dim(n.height, base_ctx, default=8)
                box = self.typesetter.diagram(n.spec, width=min(w, inner_width), height=h, number=dno)
                render_items.append(FloatItem(box=box, placement=n.placement, meta={"kind": "diagram", "number": dno}))

            elif isinstance(n, TwoColumnNode):
                cursor = engine.layout_flow(render_items, cursor, float_queue=FloatQueue(), line_gap=line_gap, auto_height=auto_height)
                render_items = []

                tw = eval_dim(n.textwidth, base_ctx, default=inner_width)
                gut = eval_dim(n.gutter, base_ctx, default=4)
                gut = max(0, min(gut, max(0, inner_width - 10)))
                tw = max(10, min(tw, inner_width))

                col_w = max(10, (tw - gut) // 2)
                col_w = min(col_w, (inner_width - gut) // 2) if (inner_width - gut) > 0 else col_w
                col_ctx = ctx_for(col_w)

                stream_items: List[Union[Box, FloatItem]] = []

                # Collect items in reading order. TextNodes are split into paragraphs so they can flow across columns.
                for child in n.children:
                    rendered_child = None
                    child_render_meta = dict(getattr(child, '_meta', {}))
                    child_render_meta["_column_width"] = col_w
                    child_render_meta["_text_width"] = inner_width
                    for ext in self.registry.render:
                        rendered_child = ext.try_render(node=child, meta=child_render_meta, compiler=self, max_width=col_w)
                        if rendered_child is not None:
                            break
                    if rendered_child is not None:
                        stream_items.append(rendered_child)
                        continue

                    if isinstance(child, TextNode):
                        txt = self.refs.resolve_text(child.text)
                        paras = re.split(r"\n\s*\n", txt.strip())
                        for p in paras:
                            p = p.strip()
                            if not p:
                                continue
                            box = self.typesetter.text(p, max_width=col_w)
                            box._role = "text"
                            stream_items.append(box)
                        continue

                    if isinstance(child, SectionNode):
                        meta_ch = getattr(child, '_meta', {})
                        secno = meta_ch.get('secno')
                        if secno is None:
                            secno = self.counters.next_section(child.level)
                            if child.label:
                                self.refs.register(child.label, secno)
                        box = self.typesetter.section(child.level, self.refs.resolve_text(child.title), number=secno, max_width=col_w)
                        box._role = "section"
                        stream_items.append(box)
                        continue

                    if isinstance(child, ColumnBreakNode):
                        stream_items.append(ColumnBreakItem())
                        continue

                    if isinstance(child, FloatBarrierNode):
                        stream_items.append(FloatBarrierItem())
                        continue

                    if isinstance(child, CodeNode):
                        stream_items.append(self.typesetter.codeblock(child.code, max_width=col_w))
                        continue

                    if isinstance(child, EquationNode):
                        meta_ch = getattr(child, '_meta', {})
                        eqno = meta_ch.get('eqno')
                        if eqno is None:
                            eqno = self.counters.next_equation()
                            if child.label:
                                self.refs.register(child.label, str(eqno))
                        stream_items.append(self.typesetter.equation(child.latex, number=eqno, max_width=col_w))
                        continue

                    if isinstance(child, ImageNode):
                        meta_ch = getattr(child, '_meta', {})
                        fno = meta_ch.get('figno')
                        if fno is None:
                            fno = self.counters.next_figure()
                            if child.label:
                                self.refs.register(child.label, str(fno))

                        width_expr = getattr(child, "width", None)
                        wants_span = isinstance(width_expr, str) and ("\\textwidth" in width_expr)
                        if wants_span:
                            w_full = eval_dim(width_expr, base_ctx, default=inner_width)
                            h_full = eval_dim(getattr(child, "height", None), base_ctx, default=10)
                            w_full = max(10, min(w_full, inner_width))
                            box_full = self.typesetter.image(child.path, width=w_full, height=h_full, number=fno)
                            # Place spanning float immediately (flush any accumulated normal items first)
                            cursor = engine.layout_flow(render_items, cursor, float_queue=FloatQueue(), line_gap=line_gap, auto_height=auto_height)
                            render_items = []
                            engine._place_box(box_full, cursor.x, cursor.y, kind="float", meta={"kind": "image", "number": fno, "span": "textwidth"})
                            cursor.y += box_full.height + line_gap
                        else:
                            w = eval_dim(width_expr, col_ctx, default=min(20, col_w))
                            h = eval_dim(getattr(child, "height", None), col_ctx, default=10)
                            box = self.typesetter.image(child.path, width=min(w, col_w), height=h, number=fno)
                            stream_items.append(FloatItem(box=box, placement=child.placement, meta={"kind": "image", "number": fno}))
                        continue

                    if isinstance(child, DiagramNode):
                        meta_ch = getattr(child, '_meta', {})
                        dno = meta_ch.get('diano')
                        if dno is None:
                            dno = self.counters.next_diagram()
                            if child.label:
                                self.refs.register(child.label, str(dno))

                        width_expr = getattr(child, "width", None)
                        wants_span = isinstance(width_expr, str) and ("\\textwidth" in width_expr)
                        if wants_span:
                            w_full = eval_dim(width_expr, base_ctx, default=inner_width)
                            h_full = eval_dim(getattr(child, "height", None), base_ctx, default=8)
                            w_full = max(10, min(w_full, inner_width))
                            box_full = self.typesetter.diagram(child.spec, width=w_full, height=h_full, number=dno)
                            cursor = engine.layout_flow(render_items, cursor, float_queue=FloatQueue(), line_gap=line_gap, auto_height=auto_height)
                            render_items = []
                            engine._place_box(box_full, cursor.x, cursor.y, kind="float", meta={"kind": "diagram", "number": dno, "span": "textwidth"})
                            cursor.y += box_full.height + line_gap
                        else:
                            w = eval_dim(width_expr, col_ctx, default=min(20, col_w))
                            h = eval_dim(getattr(child, "height", None), col_ctx, default=8)
                            box = self.typesetter.diagram(child.spec, width=min(w, col_w), height=h, number=dno)
                            stream_items.append(FloatItem(box=box, placement=child.placement, meta={"kind": "diagram", "number": dno}))
                        continue

                    if isinstance(child, BibNode):
                        # Flush everything before bibliography so the bibliography cannot "float up"
                        # into the currently shorter column (preserves reading order).
                        if stream_items:
                            cursor = engine.layout_two_columns(
                                items=stream_items,
                                cursor=cursor,
                                col_width=col_w,
                                gutter=gut,
                                balance=n.balance,
                                line_gap=line_gap,
                                auto_height=auto_height,
                            )
                            stream_items = []

                        entries = load_bib_entries(child.bibfiles, self.cite_numbers)
                        # Flowable bibliography: title + one box per entry
                        box = Box.from_lines(["REFERENCES", "─" * len("REFERENCES")], width=col_w)
                        box._role = "section"
                        stream_items.append(box)
                        for e in entries:
                            box = self.typesetter.text(e, max_width=col_w)
                            box._role = "text"
                            stream_items.append(box)
                            stream_items.append(Box.from_lines([""], width=col_w))
                        if stream_items and isinstance(stream_items[-1], Box) and stream_items[-1].lines == [""]:
                            stream_items.pop()
                        continue

                cursor = engine.layout_two_columns(
                    items=stream_items,
                    cursor=cursor,
                    col_width=col_w,
                    gutter=gut,
                    balance=n.balance,
                    line_gap=line_gap,
                    auto_height=auto_height,
                )

            elif isinstance(n, BibNode):
                entries = load_bib_entries(n.bibfiles, self.cite_numbers)
                # Expand bibliography into flowable blocks so it can paginate/flow
                bib_boxes: List[Box] = []
                title = "REFERENCES"
                underline = "─" * len(title)
                box = Box.from_lines([title, underline], width=inner_width)
                box._role = "section"
                bib_boxes.append(box)
                for e in entries:
                    box = self.typesetter.text(e, max_width=inner_width)
                    box._role = "text"
                    bib_boxes.append(box)
                    bib_boxes.append(Box.from_lines([""], width=inner_width))
                if bib_boxes and bib_boxes[-1].lines == [""]:
                    bib_boxes.pop()
                render_items.extend(bib_boxes)

        engine.layout_flow(render_items, cursor, float_queue=FloatQueue(), line_gap=line_gap, auto_height=auto_height)

        if auto_height:
            last_y = 0
            for pb in engine.placed:
                last_y = max(last_y, pb.y + pb.box.height)
            target_h = last_y + margin_bottom
            canvas.ensure_height(target_h)
            canvas.grid = canvas.grid[:target_h]
            canvas.height = target_h

        return canvas.to_string()

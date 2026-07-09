from __future__ import annotations

"""
asciitex_extended_v5_math_extension.py

Drop-in extension for `asciitex_extended_v5_bibfix.py` that renders \\begin{equation}...\\end{equation}
content using a monospace 2D math box renderer (fractions, roots, scripts, matrices, stretchy delimiters).

Key goals:
  - Equations are *not floats* (they behave like verbatim blocks / fixed blocks).
  - Works in one- and two-column layouts.
  - Keeps existing equation numbering and \\label{...} handling:
      * At top-level, uses the eqno produced by the compiler's first numbering pass.
      * Inside twocolumns (where the core compiler currently numbers equations during rendering),
        this extension assigns numbers consistently and registers labels as they are encountered.

Usage:
  from asciitex_extended_v5_bibfix import TexLikeMonospaceCompiler, ExtensionRegistry
  from asciitex_extended_v5_math_extension import AsciiMathEquationExtension

  reg = ExtensionRegistry()
  reg.add(AsciiMathEquationExtension())
  c = TexLikeMonospaceCompiler(registry=reg)
  print(c.compile(src, canvas_width=100, canvas_height=None))
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

# Import the host engine types
from asciitex import (
    Box as HostBox,
    FloatItem,
    EquationNode,
    RenderExtension,
    ParserExtension,
    TexLikeParser,
)

# =========================================================
# 2D box model (monospace) for math rendering
# =========================================================

@dataclass(frozen=True)
class MBox:
    lines: List[str]     # each line same width
    baseline: int        # row index of baseline
    width: int
    height: int

def _pad_right(s: str, w: int) -> str:
    return s[:w] if len(s) >= w else s + " " * (w - len(s))

def _normalize_lines(lines: List[str]) -> Tuple[List[str], int, int]:
    w = max((len(x) for x in lines), default=0)
    out = [_pad_right(x, w) for x in lines]
    return out, w, len(out)

def text_box(s: str) -> MBox:
    return MBox([s], baseline=0, width=len(s), height=1)

def hcat(boxes: List[MBox], gap: int = 0) -> MBox:
    if not boxes:
        return MBox([""], baseline=0, width=0, height=1)
    if len(boxes) == 1:
        return boxes[0]

    base = max(b.baseline for b in boxes)
    above = base
    below = max((b.height - 1 - b.baseline) for b in boxes)
    H = above + 1 + below

    parts: List[List[str]] = []
    total_w = 0
    for b in boxes:
        top_pad = above - b.baseline
        bottom_pad = below - (b.height - 1 - b.baseline)
        lines = [" " * b.width] * top_pad + b.lines + [" " * b.width] * bottom_pad
        parts.append(lines)
        total_w += b.width
    total_w += gap * (len(boxes) - 1)

    out_lines: List[str] = []
    for r in range(H):
        row = []
        for i, lines in enumerate(parts):
            row.append(lines[r])
            if i != len(parts) - 1 and gap:
                row.append(" " * gap)
        out_lines.append("".join(row))
    out_lines, W, _ = _normalize_lines(out_lines)
    return MBox(out_lines, baseline=base, width=W, height=H)

def vstack_boxes(boxes: List[MBox], gap: int = 0, align: str = "center", baseline_box_index: int = 0) -> MBox:
    if not boxes:
        return text_box("")
    W = max(b.width for b in boxes)

    def align_line(line: str) -> str:
        if len(line) >= W:
            return line[:W]
        pad = W - len(line)
        if align == "left":
            return line + " " * pad
        if align == "right":
            return " " * pad + line
        l = pad // 2
        r = pad - l
        return " " * l + line + " " * r

    out_lines: List[str] = []
    baseline = 0
    row_cursor = 0
    for i, b in enumerate(boxes):
        if i == baseline_box_index:
            baseline = row_cursor + b.baseline
        out_lines.extend([align_line(x) for x in b.lines])
        row_cursor += b.height
        if gap and i != len(boxes) - 1:
            out_lines.extend([" " * W] * gap)
            row_cursor += gap

    out_lines, W2, H2 = _normalize_lines(out_lines)
    return MBox(out_lines, baseline=baseline, width=W2, height=H2)

def centered_bar(width: int, ch: str = "─") -> str:
    return "" if width <= 0 else ch * width

def frac(numer: MBox, denom: MBox, bar_ch: str = "─") -> MBox:
    W = max(numer.width, denom.width) + 2
    def center(b: MBox) -> MBox:
        pad = W - b.width
        l = pad // 2
        r = pad - l
        lines = [(" " * l) + ln + (" " * r) for ln in b.lines]
        return MBox(lines, baseline=b.baseline, width=W, height=b.height)
    N = center(numer)
    D = center(denom)
    bar = text_box(centered_bar(W, bar_ch))
    lines = N.lines + bar.lines + D.lines
    baseline = len(N.lines)  # bar row
    out, W2, H2 = _normalize_lines(lines)
    return MBox(out, baseline=baseline, width=W2, height=H2)

def _overlay(canvas: List[List[str]], b: MBox, top: int, left: int) -> None:
    H = len(canvas); W = len(canvas[0]) if H else 0
    for r, ln in enumerate(b.lines):
        rr = top + r
        if rr < 0 or rr >= H: 
            continue
        for c, ch in enumerate(ln):
            cc = left + c
            if 0 <= cc < W and ch != " ":
                canvas[rr][cc] = ch

def sup_side(base: MBox, sup: MBox) -> MBox:
    raise_by = max(1, base.height // 2)
    new_baseline = base.baseline
    above = max(base.baseline, sup.baseline + raise_by)
    below = max(base.height - 1 - base.baseline, (sup.height - 1 - sup.baseline) - raise_by)
    H = above + 1 + below
    base_top = above - base.baseline
    sup_top = above - (sup.baseline + raise_by)
    W = base.width + sup.width
    canvas = [[" " for _ in range(W)] for _ in range(H)]
    _overlay(canvas, base, base_top, 0)
    _overlay(canvas, sup, sup_top, base.width)
    lines, W2, H2 = _normalize_lines(["".join(row) for row in canvas])
    return MBox(lines, baseline=new_baseline + (above - base.baseline), width=W2, height=H2)

def sub_side(base: MBox, sub: MBox) -> MBox:
    drop_by = max(1, base.height // 2)
    new_baseline = base.baseline
    above = max(base.baseline, (sub.baseline) - drop_by)
    below = max(base.height - 1 - base.baseline, (sub.height - 1 - sub.baseline) + drop_by)
    H = above + 1 + below
    base_top = above - base.baseline
    sub_top = above - (sub.baseline - drop_by)
    W = base.width + sub.width
    canvas = [[" " for _ in range(W)] for _ in range(H)]
    _overlay(canvas, base, base_top, 0)
    _overlay(canvas, sub, sub_top, base.width)
    lines, W2, H2 = _normalize_lines(["".join(row) for row in canvas])
    return MBox(lines, baseline=new_baseline + (above - base.baseline), width=W2, height=H2)

def scripts_side(base: MBox, sub: Optional[MBox], sup: Optional[MBox]) -> MBox:
    """Attach both side scripts in one shared column.

    Building superscript and subscript sequentially widens the intermediate base,
    which incorrectly pushes the second script to the right. This routine computes
    both vertical placements against the original base and overlays them at the same
    x coordinate. Tiny, representable scripts still use ``try_inline_scripts`` and
    never reach this 2D fallback.
    """
    if sub is None and sup is None:
        return base

    raise_by = max(1, base.height // 2)
    drop_by = max(1, base.height // 2)

    base_top = -base.baseline
    base_bottom = base.height - 1 - base.baseline
    min_row = base_top
    max_row = base_bottom

    if sup is not None:
        sup_top = -raise_by - sup.baseline
        sup_bottom = -raise_by + (sup.height - 1 - sup.baseline)
        min_row = min(min_row, sup_top)
        max_row = max(max_row, sup_bottom)
    if sub is not None:
        sub_top = drop_by - sub.baseline
        sub_bottom = drop_by + (sub.height - 1 - sub.baseline)
        min_row = min(min_row, sub_top)
        max_row = max(max_row, sub_bottom)

    height = max_row - min_row + 1
    script_width = max(sub.width if sub else 0, sup.width if sup else 0)
    width = base.width + script_width
    canvas = [[" " for _ in range(width)] for _ in range(height)]

    _overlay(canvas, base, base_top - min_row, 0)
    script_left = base.width
    if sup is not None:
        _overlay(canvas, sup, -raise_by - sup.baseline - min_row, script_left)
    if sub is not None:
        _overlay(canvas, sub, drop_by - sub.baseline - min_row, script_left)

    lines, width2, height2 = _normalize_lines(["".join(row) for row in canvas])
    return MBox(lines, baseline=-min_row, width=width2, height=height2)

def limits_over_under(op: MBox, sub: Optional[MBox], sup: Optional[MBox], gap: int = 0) -> MBox:
    W = op.width
    if sup is not None:
        W = max(W, sup.width)
    if sub is not None:
        W = max(W, sub.width)

    def fit_center(b: MBox) -> MBox:
        pad = max(0, W - b.width)
        l = pad // 2
        r = pad - l
        lines = [(" " * l) + ln + (" " * r) for ln in b.lines]
        return MBox(lines, baseline=b.baseline, width=W, height=b.height)

    op2 = fit_center(op)

    pieces: List[MBox] = []
    if sup is not None:
        pieces.append(fit_center(sup))
        if gap:
            pieces.append(text_box(" " * W))
    pieces.append(op2)
    if gap:
        pieces.append(text_box(" " * W))
    if sub is not None:
        pieces.append(fit_center(sub))

    baseline_box_index = pieces.index(op2)
    return vstack_boxes(pieces, gap=0, align="center", baseline_box_index=baseline_box_index)

# =========================================================
# Stretchy delimiters
# =========================================================

DELIM_PIECES = {
    # Box-drawing pieces occupy exactly one monospace cell and join on the
    # baseline. Unicode's mathematical bracket pieces have font-dependent
    # side bearings which visibly displace tall matrices and cases.
    "(": ("╭", "│", "╰"),
    ")": ("╮", "│", "╯"),
    "[": ("┌", "│", "└"),
    "]": ("┐", "│", "┘"),
    "{": ("╭", "┤", "╰"),
    "}": ("╮", "┤", "╯"),
    "|": ("│", "│", "│"),
    "||": ("║", "║", "║"),
}

def stretchy_delim(height: int, which: str) -> MBox:
    if height <= 1:
        if which == "||":
            return text_box("‖")
        if which == "|":
            return text_box("|")
        return text_box(which)

    top, mid, bot = DELIM_PIECES.get(which, ("|", "|", "|"))
    if which in ("{", "}") and height >= 3:
        extension = "│"
        middle = height // 2
        lines = [top] + [extension] * (height - 2) + [bot]
        lines[middle] = mid
    else:
        lines = [top] + [mid] * (height - 2) + [bot]
    baseline = height // 2
    return MBox(lines, baseline=baseline, width=1, height=height)

def enclose(body: MBox, left: str, right: str, pad: int = 0) -> MBox:
    L = stretchy_delim(body.height, left)
    R = stretchy_delim(body.height, right)
    inner = body
    if pad:
        inner = hcat([text_box(" " * pad), body, text_box(" " * pad)], gap=0)
    return hcat([L, inner, R], gap=0)

def abs_box(body: MBox) -> MBox:
    return enclose(body, "|", "|", pad=1)

def norm_box(body: MBox) -> MBox:
    return enclose(body, "||", "||", pad=1)

# =========================================================
# Matrix / array rendering
# =========================================================

def render_matrix(cells: List[List[MBox]], col_gap: int = 2, row_gap: int = 0) -> MBox:
    if not cells:
        return text_box("")

    nrows = len(cells)
    ncols = max((len(r) for r in cells), default=0)
    grid: List[List[MBox]] = []
    for r in cells:
        rr = r + [text_box("")] * (ncols - len(r))
        grid.append(rr)

    col_widths = [0] * ncols
    for c in range(ncols):
        col_widths[c] = max((grid[r][c].width for r in range(nrows)), default=0)

    row_above = [0] * nrows
    row_below = [0] * nrows
    row_height = [0] * nrows
    for r in range(nrows):
        above = max((grid[r][c].baseline for c in range(ncols)), default=0)
        below = max((grid[r][c].height - 1 - grid[r][c].baseline for c in range(ncols)), default=0)
        row_above[r] = above
        row_below[r] = below
        row_height[r] = above + 1 + below

    H = sum(row_height) + row_gap * (nrows - 1)
    W = sum(col_widths) + col_gap * (ncols - 1)
    canvas = [[" " for _ in range(W)] for _ in range(H)]

    y = 0
    for r in range(nrows):
        row_base_y = y + row_above[r]
        x = 0
        for c in range(ncols):
            b = grid[r][c]
            top = row_base_y - b.baseline
            pad = col_widths[c] - b.width
            left = x + (pad // 2)
            _overlay(canvas, b, top, left)
            x += col_widths[c] + col_gap
        y += row_height[r] + row_gap

    lines, W2, H2 = _normalize_lines(["".join(row) for row in canvas])
    mid_row = nrows // 2
    baseline = sum(row_height[:mid_row]) + row_gap * mid_row + row_above[mid_row]
    return MBox(lines, baseline=baseline, width=W2, height=H2)

# =========================================================
# LaTeX-ish tokenizer / AST
# =========================================================

Token = Tuple[str, str]  # (kind, value)

def tokenize(s: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "\\":
            if i + 1 < len(s) and s[i + 1] == "\\":
                tokens.append(("CMD", r"\\"))
                i += 2
                continue
            if i + 1 < len(s) and s[i + 1] == "|":
                tokens.append(("CMD", r"\|"))
                i += 2
                continue
            j = i + 1
            if j < len(s) and s[j].isalpha():
                while j < len(s) and s[j].isalpha():
                    j += 1
                tokens.append(("CMD", s[i:j]))
                i = j
            else:
                if j < len(s):
                    tokens.append(("SYM", s[j]))
                    i = j + 1
                else:
                    i += 1
            continue
        if ch in "{}_^()[]&":
            tokens.append((ch, ch))
            i += 1
            continue
        if ch in "+-*/=,;:|<>":
            tokens.append(("OP", ch))
            i += 1
            continue
        j = i
        while j < len(s) and (not s[j].isspace()) and s[j] not in "\\{}_^()[]&+-*/=,;:|<>":
            j += 1
        tokens.append(("TEXT", s[i:j]))
        i = j
    return tokens

class ParseError(Exception):
    pass

@dataclass
class Node: ...

@dataclass
class Seq(Node):
    items: List[Node]

@dataclass
class Sym(Node):
    s: str

@dataclass
class Group(Node):
    body: Node

@dataclass
class Frac(Node):
    a: Node
    b: Node

@dataclass
class Sqrt(Node):
    body: Node

@dataclass
class UnderBrace(Node):
    body: Node
    label: Optional[Node] = None

@dataclass
class Script(Node):
    base: Node
    sub: Optional[Node] = None
    sup: Optional[Node] = None
    limits: bool = False

@dataclass
class LeftRight(Node):
    left: str
    body: Node
    right: str

@dataclass
class Matrix(Node):
    kind: str
    rows: List[List[Node]]

DEFAULT_CMDS: Dict[str, str] = {
    r"\cdots": "⋯",
    r"\ldots": "…",
    r"\times": "×",
    r"\cdot": "·",
    r"\forall": "∀",
    r"\exists": "∃",
    r"\nexists": "∄",
    r"\in": "∈",
    r"\notin": "∉",
    r"\land": "∧",
    r"\lor": "∨",
    r"\le": "≤",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\approx": "≈",
    r"\to": "→",
    r"\Rightarrow": "⇒",
    r"\rightarrow": "→",
    r"\Leftrightarrow": "⇔",
    r"\leftrightarrow": "↔",
    r"\lnot": "¬",
    r"\funnymul": "⊗",
    r"\funnyplus": "⊕",
    r"\argmin": "argmin",
    r"\argmax": "argmax",
    r"\sum": "∑",
    r"\prod": "∏",
    r"\int": "∫",
    r"\,": "    ",
    r"\;": "  ",
    r"\equiv": "≡",
    r"\sim": "∼",
    r"\mathbb": "",   # special
    r"\left": "",     # special
    r"\right": "",    # special
    r"\abs": "",      # special
    r"\norm": "",     # special
    r"\begin": "",    # special
    r"\end": "",      # special
    r"\quad": "    ",
    r"\qquad": "        ",
    r"\vdash": "⊢",
    r"\models": "⊨",
    r"\vDash": "⊨",
    r"\top": "⊤",
    r"\bot": "⊥",
    r"\lightning": "↯",
    r"\blacksquare": "∎",
    r"\Box": "□",
    r"\emptyset": "∅",
    r"\cap": "∩",
    r"\cup": "∪",
    r"\setminus": "∖",
    r"\subset": "⊂",
    r"\subsetneq": "⊊",
    r"\subseteq": "⊆",
    r"\supset": "⊃",
    r"\supsetneq": "⊋",
    r"\supseteq": "⊇",
    r"\div": "÷",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\iota": "ι",
    r"\kappa": "κ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\xi": "ξ",
    r"\omikron": "ο",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\ypsilon": "υ",
    r"\phi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Alpha": "Α",
    r"\Beta": "Β",
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Epsilon": "Ε",
    r"\Zeta": "Ζ",
    r"\Eta": "Η",
    r"\Theta": "Θ",
    r"\Iota": "Ι",
    r"\Kappa": "Κ",
    r"\Lambda": "Λ",
    r"\My": "Μ",
    r"\Ny": "Ν",
    r"\Xi": "Ξ",
    r"\Omikron": "Ο",
    r"\Pi": "Π",
    r"\Rho": "Ρ",
    r"\Sigma": "Σ",
    r"\Tau": "Τ",
    r"\Ypsilon": "Υ",
    r"\Phi": "Φ",
    r"\Chi": "Χ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
    r"\circ": "∘",
    r"\landauo": "𝒪",
    r"\prime": "′",
    r"\partial": "∂",
    r"\nabla": "∇",
    r"\lvert": "|",
    r"\rvert": "|",
    r"\ast": "*",
}

SUP_MAP = {
    "0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹",
    "+":"⁺","-":"⁻","=":"⁼","(":"⁽",")":"⁾",
    "n":"ⁿ","i":"ⁱ",
    "a":"ᵃ","b":"ᵇ","c":"ᶜ","d":"ᵈ","e":"ᵉ","f":"ᶠ","g":"ᵍ","h":"ʰ","j":"ʲ","k":"ᵏ","l":"ˡ",
    "m":"ᵐ","o":"ᵒ","p":"ᵖ","r":"ʳ","s":"ˢ","t":"ᵗ","u":"ᵘ","v":"ᵛ","w":"ʷ","x":"ˣ","y":"ʸ","z":"ᶻ",
}
SUB_MAP = {
    "0":"₀","1":"₁","2":"₂","3":"₃","4":"₄","5":"₅","6":"₆","7":"₇","8":"₈","9":"₉",
    "+":"₊","-":"₋","=":"₌","(":"₍",")":"₎",
    "a":"ₐ","e":"ₑ","h":"ₕ","i":"ᵢ","j":"ⱼ","k":"ₖ","l":"ₗ","m":"ₘ","n":"ₙ","o":"ₒ",
    "p":"ₚ","r":"ᵣ","s":"ₛ","t":"ₜ","u":"ᵤ","v":"ᵥ","x":"ₓ",
}

def _node_to_plain(node) -> Optional[str]:
    if isinstance(node, Sym):
        return node.s
    if isinstance(node, Group):
        return _node_to_plain(node.body)
    if isinstance(node, Seq):
        parts = []
        for it in node.items:
            s = _node_to_plain(it)
            if s is None:
                return None
            parts.append(s)
        return "".join(parts)
    return None

def _to_sup(s: str) -> Optional[str]:
    out = []
    for ch in s:
        if ch in SUP_MAP:
            out.append(SUP_MAP[ch])
        else:
            return None
    return "".join(out)

def _to_sub(s: str) -> Optional[str]:
    out = []
    for ch in s:
        if ch in SUB_MAP:
            out.append(SUB_MAP[ch])
        else:
            return None
    return "".join(out)

def try_inline_scripts(base: MBox, sub_node, sup_node) -> Optional[MBox]:
    # Inline Unicode super/subscripts only work visually for single-line bases.
    # For tall bases such as fractions, matrices, or \left...right groups,
    # attaching the Unicode suffix via hcat() anchors it on the *baseline* of the
    # whole box, which places e.g. ^2 next to the middle of a stretched delimiter
    # instead of at the upper right. In those cases we must fall back to the 2D
    # side-placement logic (sup_side/sub_side).
    if base.height != 1:
        return None

    sub_s = _node_to_plain(sub_node) if sub_node is not None else ""
    sup_s = _node_to_plain(sup_node) if sup_node is not None else ""

    sub_u = _to_sub(sub_s) if sub_node is not None else ""
    sup_u = _to_sup(sup_s) if sup_node is not None else ""

    if (sub_node is not None and sub_u is None) or (sup_node is not None and sup_u is None):
        return None

    suffix = ""
    if sup_u:
        suffix += sup_u
    if sub_u:
        suffix += sub_u

    if not suffix:
        return None

    return hcat([base, text_box(suffix)], gap=0)

MATHBB: Dict[str, str] = {"R": "ℝ", "Q": "ℚ", "C": "ℂ", "N": "ℕ", "Z": "ℤ"}
BIGOPS = {"∑", "∏", "∫", "argmin", "argmax"}

def _is_bigop(node: Node) -> bool:
    return isinstance(node, Sym) and node.s in BIGOPS

def to_plain_text(node: Node) -> str:
    if isinstance(node, Sym): return node.s
    if isinstance(node, Group): return to_plain_text(node.body)
    if isinstance(node, Seq): return "".join(to_plain_text(x) for x in node.items)
    if isinstance(node, Frac): return f"({to_plain_text(node.a)})/({to_plain_text(node.b)})"
    if isinstance(node, Sqrt): return f"sqrt({to_plain_text(node.body)})"
    if isinstance(node, Script): return to_plain_text(node.base)
    if isinstance(node, LeftRight): return f"{node.left}{to_plain_text(node.body)}{node.right}"
    if isinstance(node, UnderBrace): return to_plain_text(node.body)
    if isinstance(node, Matrix): return "matrix"
    return ""

class Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Optional[Token]:
        return None if self.i >= len(self.toks) else self.toks[self.i]

    def pop(self) -> Optional[Token]:
        t = self.peek()
        if t is not None:
            self.i += 1
        return t

    def expect(self, kind: str) -> Token:
        t = self.pop()
        if t is None or t[0] != kind:
            raise ParseError(f"Expected {kind}, got {t}")
        return t

    def parse(self) -> Node:
        return self.parse_seq(stop=set())

    def parse_seq(self, stop: set) -> Node:
        items: List[Node] = []
        while True:
            t = self.peek()
            if t is None or t[0] in stop:
                break
            if t[0] == "CMD" and t[1] == r"\right" and "RIGHT_STOP" in stop:
                break
            items.append(self.parse_atom_with_scripts(stop))
        if len(items) == 1:
            return items[0]
        return Seq(items)

    def parse_group(self) -> Node:
        self.expect("{")
        body = self.parse_seq(stop={"}"})
        self.expect("}")
        return Group(body)

    def parse_delim_token(self) -> str:
        t = self.pop()
        if t is None:
            return "."
        kind, val = t
        if kind == "CMD" and val == r"\|":
            return "||"
        if kind == "SYM":
            if val == "{": return "{"
            if val == "}": return "}"
            return val
        if kind in ("(", ")", "[", "]"):
            return val
        if kind == "OP" and val == "|":
            return "|"
        if kind in ("TEXT", "OP"):
            return val
        return val

    def parse_matrix_env(self, env: str) -> Node:
        rows: List[List[Node]] = []
        cur_row: List[Node] = []
        cur_cell_tokens: List[Token] = []

        def flush_cell():
            nonlocal cur_cell_tokens, cur_row
            if not cur_cell_tokens:
                cur_row.append(Sym(""))
                return
            cell_ast = Parser(cur_cell_tokens).parse()
            cur_row.append(cell_ast)
            cur_cell_tokens = []

        def flush_row():
            nonlocal cur_row
            if cur_cell_tokens:
                flush_cell()
            rows.append(cur_row)
            cur_row = []

        while True:
            t = self.peek()
            if t is None:
                raise ParseError(f"Unterminated environment {env}")
            if t[0] == "CMD" and t[1] == r"\end":
                save_i = self.i
                self.pop()  # \end
                g = self.parse_group()
                name = "".join(to_plain_text(g.body).split())
                if name != env:
                    raise ParseError(f"Mismatched \\end{{{name}}} for \\begin{{{env}}}")
                break

            if t[0] == "&":
                self.pop()
                flush_cell()
                continue
            if t[0] == "CMD" and t[1] == r"\\":
                self.pop()
                flush_row()
                continue

            cur_cell_tokens.append(self.pop())

        if cur_cell_tokens or cur_row:
            flush_row()

        return Matrix(kind=env, rows=rows)

    def parse_atom(self, stop: set) -> Node:
        t = self.peek()
        if t is None:
            return Sym("")
        if t[0] == "{":
            return self.parse_group()
        if t[0] == "(":
            self.pop()
            body = self.parse_seq(stop={")"})
            self.expect(")")
            return LeftRight("(", body, ")")

        if t[0] == "CMD":
            cmd = self.pop()[1]

            if cmd == r"\frac":
                return Frac(self.parse_group(), self.parse_group())

            if cmd == r"\sqrt":
                return Sqrt(self.parse_group())

            if cmd == r"\underbrace":
                return UnderBrace(self.parse_group())

            if cmd == r"\mathbb":
                g = self.parse_group()
                key = "".join(to_plain_text(g.body).split())
                return Sym(MATHBB.get(key, f"𝔹{key}"))

            if cmd == r"\abs":
                g = self.parse_group()
                return LeftRight("|", g.body, "|")

            if cmd == r"\norm":
                g = self.parse_group()
                return LeftRight("||", g.body, "||")

            if cmd == r"\left":
                left = self.parse_delim_token()
                body = self.parse_seq(stop={"RIGHT_STOP"})
                tr = self.peek()
                if tr is None or tr[0] != "CMD" or tr[1] != r"\right":
                    raise ParseError("Expected \\right after \\left ...")
                self.pop()
                right = self.parse_delim_token()
                return LeftRight(left, body, right)

            if cmd == r"\begin":
                g = self.parse_group()
                env = "".join(to_plain_text(g.body).split())
                if env in ("matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "eqnarray", "cases"):
                    return self.parse_matrix_env(env)
                raise ParseError(f"Unsupported environment: {env}")

            return Sym(DEFAULT_CMDS.get(cmd, cmd))

        if t[0] in ("TEXT", "OP"):
            self.pop()
            return Sym(t[1])

        if t[0] == "SYM":
            self.pop()
            return Sym(t[1])

        self.pop()
        return Sym(t[1])

    def parse_script_arg(self, stop: set) -> Node:
        t = self.peek()
        if t is None:
            return Sym("")
        if t[0] == "{":
            return self.parse_group()
        return self.parse_atom(stop)

    def parse_atom_with_scripts(self, stop: set) -> Node:
        base = self.parse_atom(stop)
        sub: Optional[Node] = None
        sup: Optional[Node] = None
        while True:
            t = self.peek()
            if t is None:
                break
            if t[0] == "_":
                self.pop()
                sub = self.parse_script_arg(stop)
                continue
            if t[0] == "^":
                self.pop()
                sup = self.parse_script_arg(stop)
                continue
            break
        if sub is None and sup is None:
            return base
        if isinstance(base, UnderBrace) and sub is not None and sup is None:
            base.label = sub
            return base
        return Script(base=base, sub=sub, sup=sup, limits=_is_bigop(base))

# =========================================================
# Render AST -> MBox
# =========================================================

def render(node: Node) -> MBox:
    if isinstance(node, Sym):
        return text_box(node.s)
    if isinstance(node, Group):
        return render(node.body)
    if isinstance(node, Seq):
        if not node.items:
            return text_box("")
        result = render(node.items[0])
        previous = node.items[0]
        for item in node.items[1:]:
            compact_call = (
                isinstance(previous, Sym)
                and previous.s[-1:].isalnum()
                and isinstance(item, LeftRight)
                and item.left == "("
            )
            result = hcat([result, render(item)], gap=0 if compact_call else 1)
            previous = item
        return result
    if isinstance(node, Frac):
        return frac(render(node.a), render(node.b), bar_ch="─")
    if isinstance(node, Sqrt):
        return hcat([text_box("√"), enclose(render(node.body), "(", ")", pad=0)], gap=0)
    if isinstance(node, UnderBrace):
        body = render(node.body)
        label = render(node.label) if node.label is not None else text_box("")
        width = max(3, body.width, label.width)
        body_left = (width - body.width) // 2
        label_left = (width - label.width) // 2
        middle = width // 2
        brace = ["─"] * width
        brace[0], brace[middle], brace[-1] = "╰", "┬", "╯"
        lines = [" " * body_left + line + " " * (width - body_left - body.width) for line in body.lines]
        lines.append("".join(brace))
        lines.extend(" " * label_left + line + " " * (width - label_left - label.width) for line in label.lines)
        return MBox(lines, baseline=body.baseline, width=width, height=len(lines))
    if isinstance(node, Script):
        base = render(node.base)
        if node.limits:
            sub = render(node.sub) if node.sub else None
            sup = render(node.sup) if node.sup else None
            return limits_over_under(base, sub=sub, sup=sup, gap=0)
        inline = try_inline_scripts(base, node.sub, node.sup)
        if inline is not None:
            return inline
        sub = render(node.sub) if node.sub else None
        sup = render(node.sup) if node.sup else None
        return scripts_side(base, sub=sub, sup=sup)
    if isinstance(node, LeftRight):
        body = render(node.body)
        if node.left == "." and node.right == ".":
            return body
        if node.left == ".":
            R = stretchy_delim(body.height, node.right)
            return hcat([body, R], gap=0)
        if node.right == ".":
            L = stretchy_delim(body.height, node.left)
            return hcat([L, body], gap=0)
        compact_parentheses = body.height == 1 and node.left == "(" and node.right == ")"
        return enclose(body, node.left, node.right, pad=0 if compact_parentheses else 1)
    if isinstance(node, Matrix):
        cell_boxes: List[List[MBox]] = []
        for row in node.rows:
            cell_boxes.append([render(c) for c in row])
        M = render_matrix(cell_boxes, col_gap=2, row_gap=0)
        if node.kind == "matrix":
            return M
        if node.kind == "pmatrix":
            return enclose(M, "(", ")", pad=1)
        if node.kind == "bmatrix":
            return enclose(M, "[", "]", pad=1)
        if node.kind == "vmatrix":
            return enclose(M, "|", "|", pad=1)
        if node.kind == "Vmatrix":
            return enclose(M, "||", "||", pad=1)
        if node.kind == "eqnarray":
            return M
        if node.kind == "cases":
            return hcat([stretchy_delim(M.height, "{"), text_box(" "), M], gap=0)
        return M
    return text_box("?")

def render_math_block(expr: str) -> List[str]:
    # collapse newlines inside equation bodies
    expr = " ".join(expr.splitlines())
    toks = tokenize(expr)
    ast = Parser(toks).parse()
    b = render(ast)
    lines = [ln.rstrip() for ln in b.lines]
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines or [""]

def add_command(cmd: str, replacement: str) -> None:
    DEFAULT_CMDS[cmd] = replacement

# =========================================================
# Host-side rendering: EquationNode -> framed multi-line box
# =========================================================

def _frame_block(inner_lines: List[str], *, max_width: int, tag: str = "") -> HostBox:
    """Frame a multi-line block into a host Box of exactly max_width."""
    w_inner = max(0, max_width - 2)
    lines = [ln[:w_inner] for ln in inner_lines] or [""]
    lines = [ln.ljust(w_inner) for ln in lines]

    # Put equation tag on the last line if it fits; otherwise append a tag-only line.
    if tag:
        if len(tag) <= w_inner:
            # put at right on last line
            last = lines[-1]
            start = max(0, w_inner - len(tag))
            lines[-1] = last[:start] + tag
        else:
            # very narrow column: append a clipped tag line
            lines.append(tag[:w_inner].rjust(w_inner))

    # remove frame (Thomas 4.3.26)
    #top = "┌" + "─" * w_inner + "┐"
    #bot = "└" + "─" * w_inner + "┘"
    #mid = ["│" + ln + "│" for ln in lines]
    top = " " + " " * w_inner + " "
    bot = " " + " " * w_inner + " "
    mid = [" " + ln + " " for ln in lines]
    return HostBox.from_lines([top] + mid + [bot], width=max_width)

class AsciiMathEquationExtension(ParserExtension, RenderExtension):
    """Renders EquationNode with ASCII math, non-floating, in one- and two-column flows."""

    # --- ParserExtension: allow \label directly after \end{equation} (LaTeX-like) ---
    def try_parse(
        self,
        *,
        parser: "TexLikeParser",
        lines: List[str],
        i: int,
        pending_label: Optional[str],
    ) -> Tuple[Optional[Any], int, Optional[str]]:
        line = lines[i].strip()

        environments = {
            r"\begin{equation}": "equation",
            r"\begin{equation*}": "equation*",
            r"\begin{eqnarray}": "eqnarray",
        }
        env = environments.get(line)
        if env is None:
            return None, i, pending_label

        # Collect body until \end{equation}
        j = i + 1
        body: List[str] = []
        while j < len(lines):
            if lines[j].strip().startswith(rf"\end{{{env}}}"):
                break
            body.append(lines[j])
            j += 1

        # If unterminated, let core parser handle (it will treat as text).
        if j >= len(lines):
            return None, i, pending_label

        # Consume \end{equation}
        j += 1

        # Optional trailing \label{...} on next line (possibly with whitespace)
        trailing_label: Optional[str] = None
        if not pending_label and j < len(lines):
            m = parser._cmd_label.match(lines[j].strip())  # uses core parser's regex
            if m:
                trailing_label = m.group(1).strip()
                j += 1

        latex = "\n".join(body).strip()
        if env == "eqnarray":
            latex = rf"\begin{{eqnarray}} {latex} \end{{eqnarray}}"
        n = EquationNode(latex=latex, label=None, numbered=(env != "equation*"))

        # Prefer pending label (from a preceding \label) if present; else trailing label.
        if pending_label:
            n.label = pending_label
            pending_label = None
        elif trailing_label:
            n.label = trailing_label

        return n, j, pending_label

    def try_render(
        self,
        *,
        node: "Any",
        meta: Dict[str, Any],
        compiler: "Any",
        max_width: int,
    ) -> Optional[Union[HostBox, FloatItem]]:
        if not isinstance(node, EquationNode):
            return None

        # Determine/allocate equation number.
        eqno = meta.get("eqno")
        if eqno is None and getattr(node, "numbered", True):
            # This path is primarily for equations inside twocolumns (meta is empty in core code).
            eqno = compiler.counters.next_equation()
            if getattr(node, "label", None):
                compiler.refs.register(node.label, str(eqno))

        tag = f"({eqno})" if eqno is not None else ""
        latex = compiler.resolve_inline_text(node.latex) if hasattr(compiler, "resolve_inline_text") else (compiler.refs.resolve_text(node.latex) if hasattr(compiler, "refs") else node.latex)
        inner = render_math_block(latex)
        return _frame_block(inner, max_width=max_width, tag=tag)

# Nice alias name
AsciiMathEquation = AsciiMathEquationExtension


# =========================================================
# Monkeypatch: make balance=True preserve reading order
# =========================================================
#
# The core engine's layout_two_columns(balance=True) assigns each item to the currently
# shorter column, which can reorder blocks (it behaves like masonry).
# For equations (and in practice for any non-float blocks) we need *reading order*:
# content must flow left column -> right column without jumping ahead.
#
# This patch replaces LayoutEngine.layout_two_columns with an ordered variant:
#   - It computes an approximate target height (about half of total non-float block height)
#   - It fills the left column in order until that target is reached, then continues in the right column.
#   - Floats are still handled with basic h/t/b logic, but always in the *current* column.
#
# This fixes the "equation floats into next column/section" issue in twocolumns+balance=true.

def _install_ordered_twocolumns_patch() -> None:
    try:
        import asciitex_extended_v5_bibfix as _host
    except Exception:
        return

    LayoutEngine = getattr(_host, "LayoutEngine", None)
    Box = getattr(_host, "Box", None)
    FloatItem = getattr(_host, "FloatItem", None)
    FloatQueue = getattr(_host, "FloatQueue", None)
    LayoutCursor = getattr(_host, "LayoutCursor", None)
    if not (LayoutEngine and Box and FloatItem and FloatQueue and LayoutCursor):
        return

    def layout_two_columns_ordered(
        self,
        items,
        cursor,
        col_width,
        gutter,
        balance,
        line_gap=1,
        *,
        auto_height=False,
    ):
        # If balance is off, defer to original method.
        if not balance:
            return LayoutEngine._layout_two_columns_orig(
                self, items, cursor, col_width, gutter, balance, line_gap, auto_height=auto_height
            )

        x0, y0 = cursor.x, cursor.y
        region_h = cursor.region_height - y0

        lc = LayoutCursor(x=x0, y=y0, region_width=col_width, region_height=y0 + region_h)
        rc = LayoutCursor(x=x0 + col_width + gutter, y=y0, region_width=col_width, region_height=y0 + region_h)

        fqL, fqR = FloatQueue(), FloatQueue()
        resB_L = 0
        resB_R = 0

        def remaining_for(c, resB):
            return (c.region_height - c.y) - resB

        def place_top_floats(c, fq, resB):
            while True:
                it = fq.pop_top_that_fits(remaining_for(c, resB))
                if not it:
                    break
                self._place_box(it.box, c.x, c.y, kind="float", meta=it.meta)
                c.y += it.box.height + line_gap

        def handle_float(item, c, fq, col_tag):
            nonlocal resB_L, resB_R
            p = (item.placement or "t").lower()
            if p.startswith("b"):
                fq.bottom.append(item)
                if col_tag == "L":
                    resB_L += item.box.height + line_gap
                else:
                    resB_R += item.box.height + line_gap
                return

            if p.startswith("h"):
                resB = resB_L if col_tag == "L" else resB_R
                if auto_height or item.box.height <= remaining_for(c, resB):
                    self._place_box(item.box, c.x, c.y, kind="float", meta=item.meta)
                    c.y += item.box.height + line_gap
                    return
                fq.top.append(item)
                return

            fq.top.append(item)

        def place_box(box, c, resB):
            if (not auto_height) and box.height > remaining_for(c, resB):
                return False
            self._place_box(box, c.x, c.y, kind="block", meta={})
            c.y += box.height + line_gap
            return True

        # Approximate target height for left column (non-float blocks only).
        # This is an estimate but good enough to keep order stable.
        total = 0
        for it in items:
            if isinstance(it, FloatItem):
                continue
            total += it.height + line_gap
        target_left = y0 + max(0, total // 2)

        active = "L"  # fill left, then right
        for item in items:
            if active == "L":
                c, fq, tag, resB = lc, fqL, "L", resB_L
            else:
                c, fq, tag, resB = rc, fqR, "R", resB_R

            place_top_floats(c, fq, resB)

            if isinstance(item, FloatItem):
                handle_float(item, c, fq, tag)
                resB = resB_L if tag == "L" else resB_R
                place_top_floats(c, fq, resB)
                continue

            ok = place_box(item, c, resB)
            if not ok:
                # If it doesn't fit in current column, switch (preserving order) and place there.
                active = "R" if active == "L" else active
                c2, fq2, tag2 = (rc, fqR, "R") if active == "R" else (lc, fqL, "L")
                resB2 = resB_R if tag2 == "R" else resB_L
                place_top_floats(c2, fq2, resB2)
                ok2 = place_box(item, c2, resB2)
                if not ok2 and auto_height:
                    self._place_box(item, c2.x, c2.y, kind="block", meta={})
                    c2.y += item.height + line_gap
                continue

            # After placing a normal block, decide whether to switch to right column.
            if active == "L" and lc.y >= target_left:
                active = "R"

            resB = resB_L if tag == "L" else resB_R
            place_top_floats(c, fq, resB)

        # Flush remaining top floats
        place_top_floats(lc, fqL, resB_L)
        place_top_floats(rc, fqR, resB_R)

        # Bottom floats
        def place_bottom(fq, c, resB):
            if auto_height:
                while True:
                    it = fq.pop_bottom_any()
                    if not it:
                        break
                    self._place_box(it.box, c.x, c.y, kind="float", meta=it.meta)
                    c.y += it.box.height + line_gap
                return

            y_bottom = c.region_height - 1
            while fq.bottom:
                it = fq.pop_bottom_any()
                y_bottom -= it.box.height
                if y_bottom < c.y:
                    break
                self._place_box(it.box, c.x, y_bottom, kind="float", meta=it.meta)
                y_bottom -= line_gap

        place_bottom(fqL, lc, resB_L)
        place_bottom(fqR, rc, resB_R)

        cursor.y = max(lc.y, rc.y)
        return cursor

    # Install once
    if not hasattr(LayoutEngine, "_layout_two_columns_orig"):
        LayoutEngine._layout_two_columns_orig = LayoutEngine.layout_two_columns
        LayoutEngine.layout_two_columns = layout_two_columns_ordered

# The current core layout already preserves stream order, accounts for float
# heights while balancing, and implements real top-float placement.  The old
# compatibility patch above predates that implementation and must not override it.

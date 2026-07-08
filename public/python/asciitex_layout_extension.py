#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
List + layout extension for asciitex.

Adds:
- \begin{itemize} ... \item ... \end{itemize}
- \begin{enumerate} ... \item ... \end{enumerate}
- nested itemize/enumerate
- \verbatim{...}          (preserves unicode and spacing)
- \title{...}             (flow element)
- \quote{...}             (flow element)
- \hr                     (flow element)
- \header{...}            (page chrome; inserted above rendered content)
- \footer{...}            (page chrome; inserted below rendered content)

Design notes
- The implementation follows the same extension architecture as the Bib extension:
  parser hooks create custom nodes, render hooks turn them into Box instances.
- Header/footer are handled via a light compile wrapper that resets extension state
  and post-processes the final canvas string.
- Lists render as block elements with hanging indentation, so they work in both
  single-column and two-column layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
import re

from asciitex import (
    ParserExtension,
    RenderExtension,
    Node,
    TextNode,
    SectionNode,
    EquationNode,
    CodeNode,
    ImageNode,
    DiagramNode,
    TwoColumnNode,
    BibNode,
    Box,
    FloatItem,
    TexLikeMonospaceCompiler,
    eval_dim,
    DimContext,
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_BEGIN_LIST_RE = re.compile(r"^\s*\\begin\{(itemize|enumerate)\}\s*$")
_END_LIST_RE = re.compile(r"^\s*\\end\{(itemize|enumerate)\}\s*$")
_ITEM_RE = re.compile(r"^\s*\\item(?:\s+(.*))?\s*$")
_HR_RE = re.compile(r"^\s*\\hr\s*$")
_SIMPLE_CMD_PATTERNS = {
    "title": re.compile(r"^\s*\\title\{(.*)\}\s*$"),
    "quote": re.compile(r"^\s*\\quote\{(.*)\}\s*$"),
    "header": re.compile(r"^\s*\\header\{(.*)\}\s*$"),
    "footer": re.compile(r"^\s*\\footer\{(.*)\}\s*$"),
    "verbatim": re.compile(r"^\s*\\verbatim\{(.*)\}\s*$"),
}


def _center(text: str, width: int) -> str:
    text = text[:width]
    pad = max(0, width - len(text))
    left = pad // 2
    right = pad - left
    return (" " * left) + text + (" " * right)


def _pad_lines(lines: List[str], width: int) -> List[str]:
    return [ln[:width].ljust(width) for ln in lines]


def _balanced_inline_command(lines: List[str], i: int, cmd: str) -> Optional[Tuple[str, int]]:
    r"""Read \cmd{...} starting at lines[i], allowing multi-line balanced braces.

    Returns (content, new_i) where new_i is the first index *after* the command.
    The command may span multiple physical lines, but any trailing content after the
    closing brace must be whitespace only.
    """
    first = lines[i]
    prefix = f"\\{cmd}{{"
    if not first.startswith(prefix):
        return None

    depth = 1
    buf: List[str] = []
    line_idx = i
    col = len(prefix)

    while line_idx < len(lines):
        line = lines[line_idx]
        while col < len(line):
            ch = line[col]
            if ch == "{":
                depth += 1
                buf.append(ch)
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    if line[col + 1 :].strip():
                        return None
                    return "".join(buf), line_idx + 1
                buf.append(ch)
            else:
                buf.append(ch)
            col += 1
        if depth > 0:
            buf.append("\n")
        line_idx += 1
        col = 0

    return None


def _split_blocks(lines: List[str]) -> List[List[str]]:
    """Split preserving blank-line paragraph boundaries."""
    out: List[List[str]] = []
    cur: List[str] = []
    for ln in lines:
        if ln.strip() == "":
            if cur:
                out.append(cur)
                cur = []
            out.append([])
        else:
            cur.append(ln)
    if cur:
        out.append(cur)
    return out


def _alpha_index(n: int) -> str:
    n0 = n
    chars: List[str] = []
    while True:
        chars.append(chr(ord("a") + (n0 % 26)))
        n0 = n0 // 26 - 1
        if n0 < 0:
            break
    return "".join(reversed(chars))


def _roman_index(n: int) -> str:
    vals = [
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
        (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
        (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ]
    out: List[str] = []
    x = max(1, n)
    for v, s in vals:
        while x >= v:
            out.append(s)
            x -= v
    return "".join(out)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@dataclass
class ListItemNode(Node):
    children: List[Node]


@dataclass
class ListNode(Node):
    kind: str  # itemize | enumerate
    items: List[ListItemNode]


@dataclass
class VerbatimNode(Node):
    text: str


@dataclass
class TitleNode(Node):
    text: str


@dataclass
class QuoteNode(Node):
    text: str


@dataclass
class HrNode(Node):
    pass


@dataclass
class HeaderNode(Node):
    text: str


@dataclass
class FooterNode(Node):
    text: str


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

@dataclass
class LayoutBlocksExtension(ParserExtension, RenderExtension):
    bullet_cycle: Tuple[str, ...] = ("•", "◦", "▪")
    _header: Optional[str] = field(default=None, init=False)
    _footer: Optional[str] = field(default=None, init=False)

    def reset(self) -> None:
        self._header = None
        self._footer = None

    # ---------------------------
    # Parser
    # ---------------------------
    def try_parse(
        self,
        *,
        parser: Any,
        lines: List[str],
        i: int,
        pending_label: Optional[str],
    ) -> Tuple[Optional[Node], int, Optional[str]]:
        line = lines[i].strip()

        if _HR_RE.match(line):
            return HrNode(), i + 1, pending_label

        # Simple balanced one-argument commands.
        for cmd, node_type in (
            ("title", TitleNode),
            ("quote", QuoteNode),
            ("header", HeaderNode),
            ("footer", FooterNode),
            ("verbatim", VerbatimNode),
        ):
            parsed = _balanced_inline_command(lines, i, cmd)
            if parsed is not None:
                payload, new_i = parsed
                return node_type(text=payload), new_i, pending_label

        m = _BEGIN_LIST_RE.match(line)
        if m:
            node, new_i = self._parse_list(parser=parser, lines=lines, i=i)
            return node, new_i, pending_label

        return None, i, pending_label

    def _parse_list(self, *, parser: Any, lines: List[str], i: int) -> Tuple[ListNode, int]:
        begin = _BEGIN_LIST_RE.match(lines[i].strip())
        assert begin is not None
        kind = begin.group(1)
        i += 1
        depth = 0
        items_raw: List[List[str]] = []
        cur: Optional[List[str]] = None

        while i < len(lines):
            stripped = lines[i].strip()
            begin_nested = _BEGIN_LIST_RE.match(stripped)
            end_nested = _END_LIST_RE.match(stripped)
            item_m = _ITEM_RE.match(lines[i])

            if begin_nested:
                if depth == 0 and cur is None:
                    # tolerate content before first \item by creating an implicit item
                    cur = []
                if cur is not None:
                    cur.append(lines[i])
                depth += 1
                i += 1
                continue

            if end_nested:
                if depth > 0:
                    if cur is not None:
                        cur.append(lines[i])
                    depth -= 1
                    i += 1
                    continue
                # closes current list
                if cur is not None:
                    items_raw.append(cur)
                i += 1
                break

            if depth == 0 and item_m:
                if cur is not None:
                    items_raw.append(cur)
                cur = []
                first = item_m.group(1)
                if first:
                    cur.append(first)
                i += 1
                continue

            if cur is None:
                cur = []
            cur.append(lines[i])
            i += 1

        items: List[ListItemNode] = []
        for raw_item in items_raw:
            child_nodes = parser.parse("\n".join(raw_item).strip("\n")) if raw_item else []
            items.append(ListItemNode(children=child_nodes))

        return ListNode(kind=kind, items=items), i

    # ---------------------------
    # Rendering
    # ---------------------------
    def try_render(
        self,
        *,
        node: Node,
        meta: Dict[str, Any],
        compiler: TexLikeMonospaceCompiler,
        max_width: int,
    ) -> Optional[Union[Box, FloatItem]]:
        if isinstance(node, HeaderNode):
            self._header = node.text.strip()
            return Box.from_lines([], width=max_width)

        if isinstance(node, FooterNode):
            self._footer = node.text.strip()
            return Box.from_lines([], width=max_width)

        if isinstance(node, TitleNode):
            text = compiler.refs.resolve_text(node.text)
            text = self._replace_cites_if_available(compiler, text)
            lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()] or [""]
            out: List[str] = []
            for ln in lines:
                out.append(_center(ln, max_width))
            underline = "═" * min(max_width, max((len(ln.strip()) for ln in lines), default=0))
            if underline:
                out.append(_center(underline, max_width))
            return Box.from_lines(out, width=max_width)

        if isinstance(node, QuoteNode):
            text = compiler.refs.resolve_text(node.text)
            text = self._replace_cites_if_available(compiler, text)
            inner_w = max(10, max_width - 4)
            inner = compiler.typesetter.text(text, max_width=inner_w)
            out = ["╭" + "─" * (max_width - 2) + "╮"]
            for ln in inner.lines:
                out.append("│ " + ln[:inner_w].ljust(inner_w) + " │")
            out.append("╰" + "─" * (max_width - 2) + "╯")
            return Box.from_lines(out, width=max_width)

        if isinstance(node, HrNode):
            return Box.from_lines(["─" * max_width], width=max_width)

        if isinstance(node, VerbatimNode):
            raw_lines = node.text.splitlines() or [""]
            return Box.from_lines(_pad_lines(raw_lines, max_width), width=max_width)

        if isinstance(node, ListNode):
            return self._render_list(node=node, compiler=compiler, max_width=max_width, depth=0)

        return None

    def _replace_cites_if_available(self, compiler: TexLikeMonospaceCompiler, text: str) -> str:
        cite_map = getattr(compiler, "cite_numbers", None)
        if not cite_map:
            return text
        # reuse core helper if available in caller output; simple local version otherwise
        def repl(m: re.Match[str]) -> str:
            nums: List[str] = []
            for k in [x.strip() for x in m.group(1).split(",") if x.strip()]:
                if k not in cite_map:
                    cite_map[k] = len(cite_map) + 1
                nums.append(str(cite_map[k]))
            return "[" + ",".join(nums) + "]"
        return re.sub(r"\\cite\{([^}]+)\}", repl, text)

    def _render_list(
        self,
        *,
        node: ListNode,
        compiler: TexLikeMonospaceCompiler,
        max_width: int,
        depth: int,
    ) -> Box:
        lines: List[str] = []
        for idx, item in enumerate(node.items, start=1):
            marker = self._marker_for(node.kind, depth, idx)
            indent = len(marker) + 1
            inner_w = max(10, max_width - indent)
            child_box = self._render_item_children(item.children, compiler=compiler, max_width=inner_w, depth=depth + 1)
            child_lines = child_box.lines or ["".ljust(inner_w)]
            first = f"{marker} {child_lines[0][:inner_w].ljust(inner_w)}"
            lines.append(first[:max_width].ljust(max_width))
            follow_prefix = " " * indent
            for ln in child_lines[1:]:
                lines.append((follow_prefix + ln[:inner_w]).ljust(max_width))
            if idx != len(node.items):
                lines.append("".ljust(max_width))
        return Box.from_lines(lines, width=max_width)

    def _render_item_children(
        self,
        nodes: List[Node],
        *,
        compiler: TexLikeMonospaceCompiler,
        max_width: int,
        depth: int,
    ) -> Box:
        out: List[str] = []
        for pos, child in enumerate(nodes):
            box = self._render_child_node(child, compiler=compiler, max_width=max_width, depth=depth)
            if box is None:
                continue
            if out and box.lines:
                out.append("".ljust(max_width))
            out.extend(_pad_lines(box.lines, max_width))
        return Box.from_lines(out or ["".ljust(max_width)], width=max_width)

    def _render_child_node(
        self,
        child: Node,
        *,
        compiler: TexLikeMonospaceCompiler,
        max_width: int,
        depth: int,
    ) -> Optional[Box]:
        meta = getattr(child, "_meta", {})

        # Give extensions a chance first.
        for ext in compiler.registry.render:
            if ext is self:
                # avoid infinite recursion for list nodes; allow self for non-list cases only below
                pass
            rendered = ext.try_render(node=child, meta=meta, compiler=compiler, max_width=max_width)
            if rendered is not None:
                return rendered.box if isinstance(rendered, FloatItem) else rendered

        if isinstance(child, TextNode):
            txt = compiler.refs.resolve_text(child.text)
            txt = self._replace_cites_if_available(compiler, txt)
            return compiler.typesetter.text(txt, max_width=max_width)

        if isinstance(child, ListNode):
            return self._render_list(node=child, compiler=compiler, max_width=max_width, depth=depth)

        if isinstance(child, SectionNode):
            secno = meta.get("secno")
            txt = compiler.refs.resolve_text(child.title)
            txt = self._replace_cites_if_available(compiler, txt)
            return compiler.typesetter.section(child.level, txt, number=secno, max_width=max_width)

        if isinstance(child, EquationNode):
            eqno = meta.get("eqno")
            return compiler.typesetter.equation(child.latex, number=eqno, max_width=max_width)

        if isinstance(child, CodeNode):
            return compiler.typesetter.codeblock(child.code, max_width=max_width)

        if isinstance(child, ImageNode):
            # degrade floats to inline boxes inside list items
            ctx = DimContext(
                textwidth=max_width,
                textheight=10**9,
                columnwidth=max_width,
                canvaswidth=max_width,
                canvasheight=10**9,
            )
            fno = meta.get("figno")
            w = eval_dim(child.width, ctx, default=min(40, max_width))
            h = eval_dim(child.height, ctx, default=10)
            return compiler.typesetter.image(child.path, width=min(w, max_width), height=h, number=fno)

        if isinstance(child, DiagramNode):
            ctx = DimContext(
                textwidth=max_width,
                textheight=10**9,
                columnwidth=max_width,
                canvaswidth=max_width,
                canvasheight=10**9,
            )
            dno = meta.get("diano")
            w = eval_dim(child.width, ctx, default=min(40, max_width))
            h = eval_dim(child.height, ctx, default=8)
            return compiler.typesetter.diagram(child.spec, width=min(w, max_width), height=h, number=dno)

        if isinstance(child, TwoColumnNode):
            # Not meaningful inside a list item; render child blocks sequentially instead.
            return self._render_item_children(child.children, compiler=compiler, max_width=max_width, depth=depth + 1)

        if isinstance(child, BibNode):
            return None

        return None

    def _marker_for(self, kind: str, depth: int, idx: int) -> str:
        if kind == "itemize":
            return self.bullet_cycle[depth % len(self.bullet_cycle)]
        style = depth % 3
        if style == 0:
            return f"{idx}."
        if style == 1:
            return f"{_alpha_index(idx)}."
        return f"{_roman_index(idx)}."

    # ---------------------------
    # Post processing
    # ---------------------------
    def postprocess(self, rendered: str, compiler: TexLikeMonospaceCompiler) -> str:
        lines = rendered.splitlines()
        width = max((len(ln) for ln in lines), default=0)
        if width == 0:
            width = 1
        if self._header:
            lines.insert(0, _center(self._header, width))
        if self._footer:
            lines.append(_center(self._footer, width))
        return "\n".join(_pad_lines(lines, width))


# ---------------------------------------------------------------------------
# Compile wrapper: reset extension state + apply postprocessors
# ---------------------------------------------------------------------------

if not getattr(TexLikeMonospaceCompiler, "_asciitex_layout_blocks_patched", False):
    _ORIG_COMPILE = TexLikeMonospaceCompiler.compile

    def _compile_with_extension_reset(self: TexLikeMonospaceCompiler, *args: Any, **kwargs: Any) -> str:
        seen: List[int] = []
        exts: List[Any] = []
        for group in (self.registry.parser, self.registry.numbering, self.registry.render):
            for ext in group:
                if id(ext) not in seen:
                    seen.append(id(ext))
                    exts.append(ext)
        for ext in exts:
            reset = getattr(ext, "reset", None)
            if callable(reset):
                reset()
        out = _ORIG_COMPILE(self, *args, **kwargs)
        for ext in exts:
            post = getattr(ext, "postprocess", None)
            if callable(post):
                out = post(out, self)
        return out

    TexLikeMonospaceCompiler.compile = _compile_with_extension_reset  # type: ignore[method-assign]
    TexLikeMonospaceCompiler._asciitex_layout_blocks_patched = True


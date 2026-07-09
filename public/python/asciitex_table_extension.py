from __future__ import annotations

"""Tables, decorated boxes, and styled horizontal rules for AsciiTeX."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from asciitex import Box, DimContext, DimExpr, Node, ParserExtension, RenderExtension, eval_dim


@dataclass
class TableNode(Node):
    rows: List[List[str]]
    width: DimExpr = r"\textwidth"
    align: str = "lll"
    header: bool = True
    frame: str = "single"
    style: Optional[str] = None
    borders: Optional[str] = None
    caption: Optional[str] = None
    label: Optional[str] = None
    numbered: bool = True
    long: bool = False


@dataclass
class DecoratedBoxNode(Node):
    text: str
    width: DimExpr = r"\textwidth"
    style: str = "single"
    title: Optional[str] = None
    label: Optional[str] = None
    numbered: bool = True


@dataclass
class StyledRuleNode(Node):
    width: DimExpr = r"\textwidth"
    style: str = "single"


@dataclass(frozen=True)
class FrameChars:
    tl: str
    tj: str
    tr: str
    ml: str
    mj: str
    mr: str
    bl: str
    bj: str
    br: str
    h: str
    v: str


@dataclass(frozen=True)
class BorderSpec:
    top: bool = True
    bottom: bool = True
    left: bool = True
    right: bool = True
    header: bool = True
    rows: bool = False
    cols: bool = True


_TABLE_BEGIN_RE = re.compile(r"^\\begin\{(ascii|longascii)table\}(\[[^\]]*\])?\s*$")
_TABLE_END_RE = re.compile(r"^\\end\{(ascii|longascii)table\}\s*$")
_BOX_BEGIN_RE = re.compile(r"^\\begin\{box\}(\[[^\]]*\])?\s*$")
_BOX_END_RE = re.compile(r"^\\end\{box\}\s*$")
_RULE_RE = re.compile(r"^\\hr(?:\[([^\]]*)\])?\s*$")

_FRAME_STYLES: Dict[str, FrameChars] = {
    "single": FrameChars("┌", "┬", "┐", "├", "┼", "┤", "└", "┴", "┘", "─", "│"),
    "rounded": FrameChars("╭", "┬", "╮", "├", "┼", "┤", "╰", "┴", "╯", "─", "│"),
    "double": FrameChars("╔", "╦", "╗", "╠", "╬", "╣", "╚", "╩", "╝", "═", "║"),
    "heavy": FrameChars("┏", "┳", "┓", "┣", "╋", "┫", "┗", "┻", "┛", "━", "┃"),
    "dashed": FrameChars("┌", "┬", "┐", "├", "┼", "┤", "└", "┴", "┘", "╌", "╎"),
    "ascii": FrameChars("+", "+", "+", "+", "+", "+", "+", "+", "+", "-", "|"),
}

_RULE_CHARS = {
    "single": "─",
    "double": "═",
    "heavy": "━",
    "dashed": "╌",
    "dotted": "·",
    "ascii": "-",
}

_BORDER_PRESETS = {
    "all": BorderSpec(),
    "full": BorderSpec(),
    "grid": BorderSpec(rows=True, cols=True),
    "outer": BorderSpec(header=False, rows=False, cols=False),
    "box": BorderSpec(header=False, rows=False, cols=False),
    "none": BorderSpec(False, False, False, False, False, False, False),
    "open": BorderSpec(False, False, False, False, True, False, False),
    "top": BorderSpec(top=True, bottom=False, left=False, right=False, header=False, rows=False, cols=False),
    "bottom": BorderSpec(top=False, bottom=True, left=False, right=False, header=False, rows=False, cols=False),
    "horizontal": BorderSpec(top=True, bottom=True, left=False, right=False, header=True, rows=True, cols=False),
    "rows": BorderSpec(top=True, bottom=True, left=False, right=False, header=True, rows=True, cols=False),
    "vertical": BorderSpec(top=False, bottom=False, left=True, right=True, header=False, rows=False, cols=True),
    "cols": BorderSpec(top=False, bottom=False, left=True, right=True, header=False, rows=False, cols=True),
}


def _strip_quotes(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _truthy(value: Optional[str], default: bool = True) -> bool:
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _render_width(expr: DimExpr, meta: Dict[str, Any], max_width: int) -> int:
    text_width = int(meta.get("_text_width", max_width))
    column_width = int(meta.get("_column_width", max_width))
    ctx = DimContext(
        textwidth=text_width,
        textheight=10**9,
        columnwidth=column_width,
        canvaswidth=text_width,
        canvasheight=10**9,
    )
    return max(8, min(eval_dim(expr, ctx, default=max_width), text_width))


def _style_and_borders(frame: str, style: Optional[str], borders: Optional[str]) -> Tuple[FrameChars, BorderSpec]:
    frame = (frame or "single").strip().lower()
    style_name = (style or "").strip().lower()
    borders_name = (borders or "").strip().lower()

    if not style_name:
        style_name = frame if frame in _FRAME_STYLES else "single"
    if not borders_name:
        if frame in _BORDER_PRESETS or "," in frame:
            borders_name = frame
        else:
            borders_name = "all"

    chars = _FRAME_STYLES.get(style_name, _FRAME_STYLES["single"])
    return chars, _parse_borders(borders_name)


def _parse_borders(value: str) -> BorderSpec:
    value = (value or "all").strip().lower()
    if value in _BORDER_PRESETS:
        return _BORDER_PRESETS[value]
    tokens = {part.strip() for part in re.split(r"[,|+\s]+", value) if part.strip()}
    if "all" in tokens:
        return BorderSpec(rows=("rows" in tokens or "grid" in tokens), cols=True)
    if "none" in tokens:
        return BorderSpec(False, False, False, False, False, False, False)
    if "outer" in tokens or "box" in tokens:
        top = bottom = left = right = True
    else:
        top = "top" in tokens
        bottom = "bottom" in tokens
        left = "left" in tokens
        right = "right" in tokens
    horizontal = "horizontal" in tokens
    vertical = "vertical" in tokens
    rows = "rows" in tokens or "row" in tokens or "grid" in tokens or horizontal
    cols = "cols" in tokens or "columns" in tokens or "column" in tokens or "grid" in tokens or vertical
    header = "header" in tokens or rows or "grid" in tokens
    if horizontal:
        top = bottom = True
    if vertical:
        left = right = True
    return BorderSpec(top=top, bottom=bottom, left=left, right=right, header=header, rows=rows, cols=cols)


def _split_cell_blocks(value: str) -> List[Optional[str]]:
    """Split table cell text on TeX line breaks.

    ``\\`` starts a new visual line inside the cell. ``\\ \\`` inserts an empty
    line. The marker is intentionally handled only by table cells so ordinary
    prose keeps its existing TeX-ish parsing behavior.
    """
    marker = "\uE000"
    protected = re.sub(r"\\\\\s+\\\\", f"{marker}{marker}", value)
    protected = re.sub(r"\\\\", marker, protected)
    blocks: List[Optional[str]] = []
    for part in protected.split(marker):
        text = " ".join(part.split())
        blocks.append(text if text else None)
    return blocks or [""]


def _wrap_cell_text(value: str, width: int) -> List[str]:
    """Greedy, ragged-right cell wrapping without hyphenation or justification."""
    lines: List[str] = []
    for block in _split_cell_blocks(value):
        if block is None:
            lines.append("")
            continue
        words = block.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            while len(word) > width:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:width])
                word = word[width:]
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


class AsciiTableExtension(ParserExtension, RenderExtension):
    def try_parse(
        self,
        *,
        parser: Any,
        lines: List[str],
        i: int,
        pending_label: Optional[str],
    ) -> Tuple[Optional[Node], int, Optional[str]]:
        line = lines[i].strip()

        table_match = _TABLE_BEGIN_RE.match(line)
        if table_match:
            kind = table_match.group(1)
            opts = parser.parse_kv_opts(table_match.group(2))
            body: List[str] = []
            j = i + 1
            while j < len(lines) and not _TABLE_END_RE.match(lines[j].strip()):
                body.append(lines[j])
                j += 1
            if j >= len(lines):
                raise ValueError(f"Unterminated {kind}table beginning at line {i + 1}")
            rows = [
                [cell.strip() for cell in row.split("&")]
                for row in body
                if row.strip() and row.strip() != r"\hline"
            ]
            node = TableNode(
                rows=rows,
                width=opts.get("width", r"\textwidth"),
                align=opts.get("align", "lll").lower(),
                header=_truthy(opts.get("header"), True),
                frame=opts.get("frame", "single").lower(),
                style=(opts.get("style") or opts.get("borderstyle") or "").lower() or None,
                borders=(opts.get("borders") or opts.get("border") or "").lower() or None,
                caption=_strip_quotes(opts.get("caption")),
                numbered=_truthy(opts.get("numbered"), True),
                long=(kind == "longascii"),
            )
            if pending_label:
                node.label, pending_label = pending_label, None
            return node, j + 1, pending_label

        box_match = _BOX_BEGIN_RE.match(line)
        if box_match:
            opts = parser.parse_kv_opts(box_match.group(1))
            body = []
            j = i + 1
            while j < len(lines) and not _BOX_END_RE.match(lines[j].strip()):
                body.append(lines[j])
                j += 1
            if j >= len(lines):
                raise ValueError(f"Unterminated box beginning at line {i + 1}")
            node = DecoratedBoxNode(
                text="\n".join(body).strip(),
                width=opts.get("width", r"\textwidth"),
                style=opts.get("style", "single").lower(),
                title=_strip_quotes(opts.get("title")),
                numbered=_truthy(opts.get("numbered"), True),
            )
            if pending_label:
                node.label, pending_label = pending_label, None
            return node, j + 1, pending_label

        rule_match = _RULE_RE.match(line)
        if rule_match and rule_match.group(1) is not None:
            opts = parser.parse_kv_opts(f"[{rule_match.group(1)}]")
            return StyledRuleNode(
                width=opts.get("width", r"\textwidth"),
                style=opts.get("style", "single").lower(),
            ), i + 1, pending_label

        return None, i, pending_label

    def try_number(self, *, node: Node, meta: Dict[str, Any], counters: Any, refs: Any) -> bool:
        if isinstance(node, TableNode):
            if not node.numbered:
                return True
            number = getattr(node, "_asciitex_number", None)
            if number is None:
                number = getattr(counters, "table", 0) + 1
                setattr(counters, "table", number)
                setattr(node, "_asciitex_number", number)
            meta["tableno"] = number
        elif isinstance(node, DecoratedBoxNode):
            if not node.numbered:
                return True
            number = getattr(node, "_asciitex_number", None)
            if number is None:
                number = getattr(counters, "box", 0) + 1
                setattr(counters, "box", number)
                setattr(node, "_asciitex_number", number)
            meta["boxno"] = number
        else:
            return False
        if node.label:
            refs.register(node.label, str(number))
        return True

    def try_render(
        self,
        *,
        node: Node,
        meta: Dict[str, Any],
        compiler: Any,
        max_width: int,
    ) -> Optional[Union[Box, Any]]:
        if isinstance(node, TableNode):
            return self._render_table(node, meta, compiler, max_width)
        if isinstance(node, DecoratedBoxNode):
            return self._render_box(node, meta, compiler, max_width)
        if isinstance(node, StyledRuleNode):
            width = _render_width(node.width, meta, max_width)
            char = _RULE_CHARS.get(node.style, _RULE_CHARS["single"])
            return Box.from_lines([char * width], width=width)
        return None

    def _render_box(self, node: DecoratedBoxNode, meta: Dict[str, Any], compiler: Any, max_width: int) -> Box:
        width = _render_width(node.width, meta, max_width)
        frame = _FRAME_STYLES.get(node.style, _FRAME_STYLES["single"])
        inner_width = max(1, width - 4)
        resolve = compiler.resolve_inline_text if hasattr(compiler, "resolve_inline_text") else compiler.refs.resolve_text
        inner = compiler.typesetter.text(resolve(node.text), max_width=inner_width)
        boxno = meta.get("boxno")
        resolved_title = resolve(node.title) if node.title else node.title
        heading = f"Box {boxno}: {resolved_title}" if boxno is not None and resolved_title else (f"Box {boxno}" if boxno is not None else resolved_title)
        title = f" {heading} " if heading else ""
        top_fill = max(0, width - 2 - len(title))
        top = frame.tl + title + frame.h * top_fill + frame.tr
        lines = [top[:width].ljust(width)]
        lines.extend(frame.v + " " + line[:inner_width].ljust(inner_width) + " " + frame.v for line in inner.lines)
        lines.append(frame.bl + frame.h * (width - 2) + frame.br)
        return Box.from_lines(lines, width=width)

    def _render_table(self, node: TableNode, meta: Dict[str, Any], compiler: Any, max_width: int) -> Box:
        width = _render_width(node.width, meta, max_width)
        resolve = compiler.resolve_inline_text if hasattr(compiler, "resolve_inline_text") else compiler.refs.resolve_text
        rows = [[resolve(cell) for cell in row] for row in node.rows]
        if not rows:
            return Box.from_lines([], width=width)
        column_count = max(len(row) for row in rows)
        rows = [row + [""] * (column_count - len(row)) for row in rows]
        frame, borders = _style_and_borders(node.frame, node.style, node.borders)
        widths = self._column_widths(rows, column_count, width, borders, node.long)

        lines: List[str] = []
        if node.caption:
            tableno = meta.get("tableno")
            caption_text = resolve(node.caption)
            caption = f"Table {tableno}: {caption_text}" if tableno is not None else caption_text
            lines.append(caption[:width].center(width))
        if borders.top:
            lines.append(self._border_line(width, widths, frame, borders, "top"))
        for index, row in enumerate(rows):
            rendered_row = self._render_row(row, widths, node.align, borders, frame, compiler, node.long)
            lines.extend(rendered_row)
            if index == 0 and node.header and borders.header and len(rows) > 1:
                lines.append(self._border_line(width, widths, frame, borders, "middle"))
            elif index < len(rows) - 1 and borders.rows:
                lines.append(self._border_line(width, widths, frame, borders, "middle"))
        if borders.bottom:
            lines.append(self._border_line(width, widths, frame, borders, "bottom"))
        return Box.from_lines([line[:width].ljust(width) for line in lines], width=width)

    def _column_widths(self, rows: List[List[str]], column_count: int, table_width: int, borders: BorderSpec, long: bool) -> List[int]:
        left = 1 if borders.left else 0
        right = 1 if borders.right else 0
        between = 1 if borders.cols else 2
        available = table_width - left - right - between * (column_count - 1) - 2 * column_count
        available = max(column_count, available)
        widths = [max(1, available // column_count) for _ in range(column_count)]
        for col in range(available % column_count):
            widths[col] += 1
        if not long:
            natural = [max(len(row[col]) for row in rows) for col in range(column_count)]
            if sum(natural) <= available:
                widths = natural[:]
                for col in range(available - sum(widths)):
                    widths[col % column_count] += 1
        return widths

    def _render_row(
        self,
        row: List[str],
        widths: List[int],
        align: str,
        borders: BorderSpec,
        frame: FrameChars,
        compiler: Any,
        long: bool,
    ) -> List[str]:
        rendered_cells: List[List[str]] = []
        for col, value in enumerate(row):
            if long:
                rendered_cells.append([line[:widths[col]] for line in _wrap_cell_text(value, widths[col])])
            else:
                rendered_cells.append([value[:widths[col]]])
        height = max(len(cell) for cell in rendered_cells)
        out: List[str] = []
        for line_no in range(height):
            cells: List[str] = []
            for col, cell in enumerate(rendered_cells):
                value = cell[line_no] if line_no < len(cell) else ""
                alignment = align[col] if col < len(align) else "l"
                if alignment == "r":
                    value = value.rjust(widths[col])
                elif alignment == "c":
                    value = value.center(widths[col])
                else:
                    value = value.ljust(widths[col])
                cells.append(f" {value} ")
            joiner = frame.v if borders.cols else "  "
            line = joiner.join(cells)
            if borders.left:
                line = frame.v + line
            if borders.right:
                line += frame.v
            out.append(line)
        return out

    def _border_line(self, width: int, widths: List[int], frame: FrameChars, borders: BorderSpec, position: str) -> str:
        if not borders.cols:
            if position == "top":
                left, right = frame.tl, frame.tr
            elif position == "bottom":
                left, right = frame.bl, frame.br
            else:
                left, right = frame.ml, frame.mr
            if borders.left and borders.right:
                return left + frame.h * max(0, width - 2) + right
            if borders.left:
                return left + frame.h * max(0, width - 1)
            if borders.right:
                return frame.h * max(0, width - 1) + right
            return frame.h * width
        if position == "top":
            left, join, right = frame.tl, frame.tj, frame.tr
        elif position == "bottom":
            left, join, right = frame.bl, frame.bj, frame.br
        else:
            left, join, right = frame.ml, frame.mj, frame.mr
        core = join.join(frame.h * (w + 2) for w in widths)
        if borders.left and borders.right:
            return left + core + right
        if borders.left:
            return left + core
        if borders.right:
            return core + right
        return core


TableExtension = AsciiTableExtension

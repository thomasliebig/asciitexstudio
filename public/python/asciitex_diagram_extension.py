
from __future__ import annotations

"""
asciitex_diagram_extension.py

Extension for `asciitex.py` that upgrades diagrams into real
Unicode/ASCII plots (scatter/lines/hist/box) and keeps LaTeX-like float behavior
in both single-column flow and `twocolumns`.

Key features
- Parses an enhanced \\begindiagram[...]/\\enddiagram environment.
- Produces Unicode plots using a pure-text plotting backend.
- Diagrams are floats (t/b/h), support width/height with \\textwidth / \\columnwidth.
- Works inside twocolumns, including full-width spanning floats when width=\\textwidth.
- Optional: execute Python inside the diagram body (safe-ish sandbox) to generate data.

Body modes
- mode=python (default): body is Python that must set `__diagram__` to either:
    * a plot-spec dict (see below), or
    * a ready-to-render string (full diagram content).
- mode=spec: body is a Python literal (via ast.literal_eval) with the same plot-spec dict.

Plot-spec dict schema (minimal)
{
  "type": "scatter" | "lines" | "hist" | "box",
  "title": "Optional title",
  "x_label": "x",
  "y_label": "y",
  "grid": True/False,
  "legend": True/False,

  # for scatter/lines:
  "scatters": [{"x":[...], "y":[...], "name":"...", "marker":"o"}, ...],
  "lines":    [{"x":[...], "y":[...], "name":"...", "ch":"·", "dashed":False}, ...],

  # for hist:
  "hist": {"values":[...], "bins":30, "name":"N(0,1)", "shade":"█"},

  # for box:
  "box": {"groups":[[...],[...]], "labels":["A","B"], "name":"distribution"}
}

Caption
- caption="..." (optional). If omitted: "Diagram <n>: <title/type>"

"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
import ast
import math
import random
import re

from asciitex import (
    ParserExtension,
    NumberingExtension,
    RenderExtension,
    TexLikeParser,
    Node as HostNode,
    Box as HostBox,
    FloatItem,
    DimExpr,
    DimContext,
    eval_dim,
    LayoutEngine,
    LayoutCursor,
)


# ============================================================
# Two-column engine patch: support full-width spanning floats
# ============================================================

_ORIG_LAYOUT_TWO_COLUMNS = None

def _patch_layout_two_columns_for_fullwidth_spans() -> None:
    global _ORIG_LAYOUT_TWO_COLUMNS
    if _ORIG_LAYOUT_TWO_COLUMNS is not None:
        return

    _ORIG_LAYOUT_TWO_COLUMNS = LayoutEngine.layout_two_columns

    def layout_two_columns_patched(
        self: LayoutEngine,
        items: List[Union[HostBox, FloatItem]],
        cursor: LayoutCursor,
        col_width: int,
        gutter: int,
        balance: bool,
        line_gap: int = 1,
        *,
        auto_height: bool = False,
    ) -> LayoutCursor:
        segments: List[List[Union[HostBox, FloatItem]]] = []
        cur: List[Union[HostBox, FloatItem]] = []
        span_items: List[Tuple[int, FloatItem]] = []

        for it in items:
            if isinstance(it, FloatItem) and bool(it.meta.get("span_full")):
                segments.append(cur)
                cur = []
                span_items.append((len(segments) - 1, it))
            else:
                cur.append(it)
        segments.append(cur)

        cur_cursor = cursor
        for seg_idx, seg in enumerate(segments):
            if seg:
                cur_cursor = _ORIG_LAYOUT_TWO_COLUMNS(
                    self, seg, cur_cursor, col_width, gutter, balance, line_gap, auto_height=auto_height
                )
            for idx, span in span_items:
                if idx == seg_idx:
                    box = span.box
                    if (not auto_height) and box.height > (cur_cursor.region_height - cur_cursor.y):
                        return cur_cursor
                    self._place_box(box, cur_cursor.x, cur_cursor.y, kind="float", meta=span.meta)
                    cur_cursor.y += box.height + line_gap
        return cur_cursor

    LayoutEngine.layout_two_columns = layout_two_columns_patched


# ============================================================
# Plot backend (Unicode text plotting)
# ============================================================

def _nice_num(x: float) -> float:
    if x == 0:
        return 0.0
    exp = math.floor(math.log10(abs(x)))
    f = abs(x) / (10 ** exp)
    if f < 1.5:
        nf = 1.0
    elif f < 3.5:
        nf = 2.0
    elif f < 7.5:
        nf = 5.0
    else:
        nf = 10.0
    return math.copysign(nf * (10 ** exp), x)

def _nice_ticks(vmin: float, vmax: float, nticks: int) -> List[float]:
    if vmin == vmax:
        return [vmin]
    span = vmax - vmin
    step = _nice_num(span / max(1, nticks - 1))
    t0 = math.floor(vmin / step) * step
    t1 = math.ceil(vmax / step) * step
    ticks: List[float] = []
    t = t0
    for _ in range(10000):
        if t > t1 + step * 0.5:
            break
        ticks.append(t)
        t += step
    return ticks

def _fmt_tick(v: float, maxlen: int = 7) -> str:
    if v == 0:
        s = "0"
    elif abs(v) >= 1e4 or abs(v) < 1e-3:
        s = f"{v:.1e}"
    else:
        s = f"{v:.4f}".rstrip("0").rstrip(".")
    if len(s) > maxlen:
        s = s[:maxlen]
    return s

def _quantile(sorted_x: List[float], q: float) -> float:
    if not sorted_x:
        return float("nan")
    n = len(sorted_x)
    if n == 1:
        return sorted_x[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_x[lo]
    w = pos - lo
    return sorted_x[lo] * (1 - w) + sorted_x[hi] * w

def _log10_safe(x: float) -> float:
    if x <= 0:
        return float("nan")
    return math.log10(x)


def _wrap_words(text: str, width: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    width = max(1, int(width))
    words = text.split()
    lines: List[str] = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

@dataclass
class AxisSpec:
    label: str = ""
    log: bool = False
    ticks: int = 6
    ticks_values: Optional[List[float]] = None
    ticklabels: Optional[List[str]] = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None

@dataclass
class Style:
    marker: str = "o"
    line: str = "·"
    dashed: bool = False

@dataclass
class PlotSpec:
    width: int = 80
    height: int = 24
    title: str = ""
    x: AxisSpec = field(default_factory=lambda: AxisSpec("x", log=False, ticks=6))
    y: AxisSpec = field(default_factory=lambda: AxisSpec("y", log=False, ticks=6))
    grid: bool = True
    legend: bool = True

@dataclass
class ScatterSeries:
    x: List[float]
    y: List[float]
    name: str = "series"
    style: Style = field(default_factory=lambda: Style(marker="o"))

@dataclass
class LineSeries:
    x: List[float]
    y: List[float]
    name: str = "line"
    style: Style = field(default_factory=lambda: Style(line="·", dashed=False))

@dataclass
class Histogram:
    values: List[float]
    bins: int = 20
    name: str = "hist"
    shade: str = "█"

@dataclass
class BoxPlot:
    groups: List[List[float]]
    labels: Optional[List[str]] = None
    name: str = "box"

class TextCanvas:
    def __init__(self, width: int, height: int, fill: str = " "):
        if width < 10 or height < 6:
            raise ValueError("Canvas too small; use at least width>=10, height>=6.")
        self.w = width
        self.h = height
        self.buf = [[fill] * width for _ in range(height)]

    def set(self, x: int, y: int, ch: str):
        if 0 <= x < self.w and 0 <= y < self.h and ch:
            self.buf[y][x] = ch

    def get(self, x: int, y: int) -> str:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.buf[y][x]
        return " "

    def write(self, x: int, y: int, s: str):
        for i, ch in enumerate(s):
            self.set(x + i, y, ch)

    def render(self) -> str:
        return "\n".join("".join(row) for row in self.buf)

    def draw_hline(self, x0: int, x1: int, y: int, ch: str = "─"):
        if x1 < x0:
            x0, x1 = x1, x0
        for x in range(x0, x1 + 1):
            self.set(x, y, ch)

    def draw_vline(self, x: int, y0: int, y1: int, ch: str = "│"):
        if y1 < y0:
            y0, y1 = y1, y0
        for y in range(y0, y1 + 1):
            self.set(x, y, ch)

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, ch: str = "·", dashed: bool = False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        n = 0
        while True:
            if (not dashed) or (n % 2 == 0):
                self.set(x0, y0, ch)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
            n += 1

def unicode_plot(
    *,
    spec: PlotSpec,
    scatters: Optional[List[ScatterSeries]] = None,
    lines: Optional[List[LineSeries]] = None,
    hist: Optional[Histogram] = None,
    box: Optional[BoxPlot] = None,
) -> str:
    scatters = scatters or []
    lines = lines or []

    c = TextCanvas(spec.width, spec.height)

    y_custom_ticks = spec.y.ticks_values or []
    y_tick_strs = [str(t) for t in (spec.y.ticklabels or [])]
    y_num_strs = [_fmt_tick(v) for v in y_custom_ticks] if y_custom_ticks else []
    y_tick_label_w = max([len(s) for s in (y_tick_strs + y_num_strs)] + [0])

    y_label_lines = _wrap_words(spec.y.label, 12) if spec.y.label else []
    y_label_w = max([len(s) for s in y_label_lines] + [0])

    ylab_w = max(9, y_tick_label_w + 2, y_label_w + 1)
    xlab_h = 3
    top_h = 2 + (1 if (spec.legend and (scatters or lines or hist or box)) else 0)

    plot_x0 = ylab_w
    plot_y0 = top_h
    plot_x1 = spec.width - 2
    plot_y1 = spec.height - xlab_h - 2

    if plot_x1 <= plot_x0 + 3 or plot_y1 <= plot_y0 + 3:
        raise ValueError("Plot area too small; increase diagram width/height.")

    xs: List[float] = []
    ys: List[float] = []

    if hist is not None:
        vals = [v for v in hist.values if math.isfinite(v)]
        if not vals:
            return "(empty histogram)"
        xs = vals[:]
        x_min, x_max = min(xs), max(xs)
        if x_min == x_max:
            x_min -= 0.5
            x_max += 0.5
        if spec.x.min_val is not None:
            x_min = float(spec.x.min_val)
        if spec.x.max_val is not None:
            x_max = float(spec.x.max_val)
        y_min, y_max = 0.0, 1.0
        if spec.y.min_val is not None:
            y_min = float(spec.y.min_val)
        if spec.y.max_val is not None:
            y_max = float(spec.y.max_val)

    elif box is not None:
        allv = [v for g in box.groups for v in g if math.isfinite(v)]
        if not allv:
            return "(empty boxplot)"
        y_min, y_max = min(allv), max(allv)
        if y_min == y_max:
            y_min -= 0.5
            y_max += 0.5
        x_min, x_max = 0.5, len(box.groups) + 0.5
        if spec.x.min_val is not None:
            x_min = float(spec.x.min_val)
        if spec.x.max_val is not None:
            x_max = float(spec.x.max_val)
        if spec.y.min_val is not None:
            y_min = float(spec.y.min_val)
        if spec.y.max_val is not None:
            y_max = float(spec.y.max_val)

    else:
        for s in scatters:
            xs.extend([v for v in s.x if math.isfinite(v)])
            ys.extend([v for v in s.y if math.isfinite(v)])
        for l in lines:
            xs.extend([v for v in l.x if math.isfinite(v)])
            ys.extend([v for v in l.y if math.isfinite(v)])
        if not xs or not ys:
            return "(empty plot)"
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if x_min == x_max:
            x_min -= 0.5
            x_max += 0.5
        if y_min == y_max:
            y_min -= 0.5
            y_max += 0.5
        if spec.x.min_val is not None:
            x_min = float(spec.x.min_val)
        if spec.x.max_val is not None:
            x_max = float(spec.x.max_val)
        if spec.y.min_val is not None:
            y_min = float(spec.y.min_val)
        if spec.y.max_val is not None:
            y_max = float(spec.y.max_val)
        elif len(ys) > 1:
            y_span = y_max - y_min
            pad = 0.05 * y_span if y_span > 0 else 1.0
            y_min -= pad
            y_max += pad
        if spec.x.min_val is None and spec.x.max_val is None and len(xs) > 1:
            x_span = x_max - x_min
            x_pad = 0.05 * x_span if x_span > 0 else 0.5
            x_min -= x_pad
            x_max += x_pad

    def tx(v: float) -> float:
        return _log10_safe(v) if spec.x.log else v

    def ty(v: float) -> float:
        return _log10_safe(v) if spec.y.log else v

    X0, X1 = tx(x_min), tx(x_max)
    Y0, Y1 = ty(y_min), ty(y_max)

    if not math.isfinite(X0) or not math.isfinite(X1):
        raise ValueError("Invalid x-range for log scale (requires all x>0).")
    if not math.isfinite(Y0) or not math.isfinite(Y1):
        raise ValueError("Invalid y-range for log scale (requires all y>0).")

    if X0 == X1:
        X0 -= 1
        X1 += 1
    if Y0 == Y1:
        Y0 -= 1
        Y1 += 1

    pw = (plot_x1 - plot_x0)
    ph = (plot_y1 - plot_y0)

    def x_to_col(x: float) -> int:
        u = (tx(x) - X0) / (X1 - X0)
        u = min(1.0, max(0.0, u))
        return plot_x0 + int(round(u * pw))

    def y_to_row(y: float) -> int:
        u = (ty(y) - Y0) / (Y1 - Y0)
        u = min(1.0, max(0.0, u))
        return plot_y1 - int(round(u * ph))

    # frame
    c.set(plot_x0, plot_y0, "┌")
    c.set(plot_x1, plot_y0, "┐")
    c.set(plot_x0, plot_y1, "└")
    c.set(plot_x1, plot_y1, "┘")
    c.draw_hline(plot_x0 + 1, plot_x1 - 1, plot_y0, "─")
    c.draw_hline(plot_x0 + 1, plot_x1 - 1, plot_y1, "─")
    c.draw_vline(plot_x0, plot_y0 + 1, plot_y1 - 1, "│")
    c.draw_vline(plot_x1, plot_y0 + 1, plot_y1 - 1, "│")

    # title
    if spec.title:
        title = spec.title[: spec.width - 2]
        c.write(max(0, (spec.width - len(title)) // 2), 0, title)

    # x ticks
    xt = spec.x.ticks_values if spec.x.ticks_values is not None else _nice_ticks(x_min, x_max, spec.x.ticks)
    xt_labels = spec.x.ticklabels
    for i, v in enumerate(xt):
        col = x_to_col(v)
        c.set(col, plot_y1, "┴" if c.get(col, plot_y1) == "─" else "┼")
        if spec.grid:
            for yy in range(plot_y0 + 1, plot_y1):
                ex = c.get(col, yy)
                c.set(col, yy, "┆" if ex == " " else ex)
        if xt_labels and i < len(xt_labels):
            lab = str(xt_labels[i])
        else:
            lab = ("10^" + _fmt_tick(tx(v))) if spec.x.log else _fmt_tick(v)
        ylab = plot_y1 + 1
        xlab = max(plot_x0, min(col - len(lab) // 2, plot_x1 - len(lab)))
        c.write(xlab, ylab, lab)

    # y ticks
    yt = spec.y.ticks_values if spec.y.ticks_values is not None else _nice_ticks(y_min, y_max, spec.y.ticks)
    yt_labels = spec.y.ticklabels
    for i, v in enumerate(yt):
        row = y_to_row(v)
        c.set(plot_x0, row, "├" if c.get(plot_x0, row) == "│" else "┼")
        if spec.grid:
            for xx in range(plot_x0 + 1, plot_x1):
                ex = c.get(xx, row)
                c.set(xx, row, "┄" if ex == " " else ex)
        if yt_labels and i < len(yt_labels):
            lab = str(yt_labels[i])
        else:
            lab = ("10^" + _fmt_tick(ty(v))) if spec.y.log else _fmt_tick(v)
        c.write(max(0, ylab_w - 1 - len(lab)), row, lab)

    # axis labels
    if spec.x.label:
        xl = spec.x.label[: (plot_x1 - plot_x0 + 1)]
        c.write(max(plot_x0, (plot_x0 + plot_x1 - len(xl)) // 2), plot_y1 + 2, xl)
    if y_label_lines:
        max_rows = max(1, plot_y1 - plot_y0 + 1)
        lines_to_draw = y_label_lines[:max_rows]
        start_row = plot_y0
        if len(lines_to_draw) < max_rows:
            start_row = plot_y0 + max(0, (max_rows - len(lines_to_draw)) // 2)
        for off, yl in enumerate(lines_to_draw):
            c.write(0, start_row + off, yl[: ylab_w - 1])

    legend_items: List[Tuple[str, str]] = []

    # hist
    if hist is not None:
        vals = [v for v in hist.values if math.isfinite(v)]
        bins = max(1, int(hist.bins))
        bw = (x_max - x_min) / bins
        counts = [0] * bins
        for v in vals:
            i = int((v - x_min) / bw)
            if i == bins:
                i -= 1
            if 0 <= i < bins:
                counts[i] += 1
        maxc = max(counts) if counts else 1
        Y0h, Y1h = 0.0, float(max(1, maxc))

        def ycount_to_row(cn: float) -> int:
            u = (cn - Y0h) / (Y1h - Y0h)
            u = min(1.0, max(0.0, u))
            return plot_y1 - int(round(u * ph))

        for i, cn in enumerate(counts):
            left = x_min + i * bw
            right = left + bw
            col0 = x_to_col(left)
            col1 = x_to_col(right)
            top = ycount_to_row(float(cn))
            for col in range(min(col0, col1), max(col0, col1) + 1):
                for row in range(top, plot_y1):
                    if plot_x0 < col < plot_x1 and plot_y0 < row < plot_y1:
                        c.set(col, row, hist.shade)
        legend_items.append((hist.shade, hist.name))

    # box
    if box is not None:
        labels = list(box.labels) if box.labels is not None else [str(i+1) for i in range(len(box.groups))]
        for gi, g in enumerate(box.groups, start=1):
            data = sorted([v for v in g if math.isfinite(v)])
            if not data:
                continue
            q1 = _quantile(data, 0.25)
            q2 = _quantile(data, 0.50)
            q3 = _quantile(data, 0.75)
            iqr = q3 - q1
            lo = max(min(data), q1 - 1.5 * iqr)
            hi = min(max(data), q3 + 1.5 * iqr)

            col = x_to_col(float(gi))
            r_q1 = y_to_row(q1); r_q2 = y_to_row(q2); r_q3 = y_to_row(q3)
            r_lo = y_to_row(lo); r_hi = y_to_row(hi)

            c.draw_vline(col, r_hi, r_lo, "│")
            c.draw_hline(col - 2, col + 2, r_hi, "─")
            c.draw_hline(col - 2, col + 2, r_lo, "─")

            top = min(r_q3, r_q1)
            bot = max(r_q3, r_q1)
            c.set(col - 3, top, "┌"); c.set(col + 3, top, "┐")
            c.set(col - 3, bot, "└"); c.set(col + 3, bot, "┘")
            c.draw_hline(col - 2, col + 2, top, "─")
            c.draw_hline(col - 2, col + 2, bot, "─")
            c.draw_vline(col - 3, top + 1, bot - 1, "│")
            c.draw_vline(col + 3, top + 1, bot - 1, "│")
            for rr in range(top + 1, bot):
                for cc in range(col - 2, col + 3):
                    if plot_x0 < cc < plot_x1 and plot_y0 < rr < plot_y1 and c.get(cc, rr) == " ":
                        c.set(cc, rr, "░")
            c.draw_hline(col - 2, col + 2, r_q2, "━")

            lab = labels[gi - 1][:6]
            c.write(max(plot_x0, min(col - len(lab) // 2, plot_x1 - len(lab))), plot_y1 + 1, lab)

        legend_items.append(("░", box.name))

    # lines
    for l in lines:
        pts = [(x_to_col(x), y_to_row(y)) for x, y in zip(l.x, l.y) if math.isfinite(x) and math.isfinite(y)]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            c.draw_line(x0, y0, x1, y1, ch=l.style.line, dashed=l.style.dashed)
        legend_items.append((l.style.line, l.name))

    # scatters
    for s in scatters:
        for x, y in zip(s.x, s.y):
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            col = x_to_col(x); row = y_to_row(y)
            if plot_x0 <= col <= plot_x1 and plot_y0 <= row <= plot_y1:
                c.set(col, row, s.style.marker)
        legend_items.append((s.style.marker, s.name))

    # legend
    if spec.legend and legend_items:
        y_leg = 1 if spec.title else 0
        x_leg = plot_x0
        parts = []
        seen = set()
        for sym, name in legend_items:
            key = (sym, name)
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"{sym} {name}")
        leg = "  ".join(parts)
        leg = leg[: spec.width - x_leg - 1]
        c.write(x_leg, y_leg, leg)

    return c.render()


# ============================================================
# Node definition + parser
# ============================================================

@dataclass
class PlotDiagramNode(HostNode):
    body: str
    width: DimExpr = r"\columnwidth"
    height: DimExpr = 24
    placement: str = "t"
    mode: str = "python"         # python | spec
    caption: Optional[str] = None
    frame: bool = False
    label: Optional[str] = None
    numbered: bool = True


class DiagramPlotExtension(ParserExtension, NumberingExtension, RenderExtension):
    """
    Enhanced diagram environment:

        \\begindiagram[width=...,height=...,place=t,mode=python,type=scatter,caption="..."]
        ... body ...
        \\enddiagram

    Notes:
      - The parser stores the raw body plus options.
      - Rendering executes/decodes the body to a plot-spec dict, then renders.
      - If `width=\\textwidth` inside twocolumns, it becomes a spanning float.
    """

    _begin = re.compile(r"^\\begindiagram(\[[^\]]*\])?\s*$")
    _end = re.compile(r"^\\enddiagram\s*$")

    @staticmethod
    def _strip_quotes(v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if (len(s) >= 2) and ((s[0] == s[-1] == "'") or (s[0] == s[-1] == '"')):
            return s[1:-1]
        return s

    def try_parse(
        self,
        *,
        parser: TexLikeParser,
        lines: List[str],
        i: int,
        pending_label: Optional[str],
    ) -> Tuple[Optional[HostNode], int, Optional[str]]:
        m = self._begin.match(lines[i].strip())
        if not m:
            return None, i, pending_label

        opts = parser.parse_kv_opts(m.group(1))
        width: DimExpr = opts.get("width", r"\columnwidth")
        height: DimExpr = opts.get("height", "24")
        placement = opts.get("place", "t")
        mode = (opts.get("mode", "python") or "python").strip().lower()

        # forgiving caption key
        caption = opts.get("caption")
        if caption is None:
            for k in list(opts.keys()):
                if k.strip().lower() == "caption":
                    caption = opts[k]
                    break
        caption = self._strip_quotes(caption)

        def _get_bool(key: str, default: bool) -> bool:
            v = opts.get(key, None)
            if v is None:
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        frame = _get_bool("frame", False)

        i += 1
        body_lines: List[str] = []
        while i < len(lines) and not self._end.match(lines[i].strip()):
            body_lines.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1  # consume end

        node = PlotDiagramNode(
            body="\n".join(body_lines),
            width=width,
            height=height,
            placement=placement,
            mode=mode,
            caption=caption,
            frame=frame,
            numbered=_get_bool("numbered", True),
        )

        if pending_label:
            node.label = pending_label
            pending_label = None

        _patch_layout_two_columns_for_fullwidth_spans()
        return node, i, pending_label

    def try_number(self, *, node: HostNode, meta: Dict[str, Any], counters: Any, refs: Any) -> bool:
        if not isinstance(node, PlotDiagramNode):
            return False
        if not node.numbered:
            return True
        dno = counters.next_diagram()
        meta["diano"] = dno
        if getattr(node, "label", None):
            refs.register(node.label, str(dno))
        return True

    # --- plot-spec loading ---

    @staticmethod
    def _load_spec_from_body(node: PlotDiagramNode) -> Union[str, Dict[str, Any]]:
        """
        Returns either:
          - a diagram string (already rendered), OR
          - a plot-spec dict.
        """
        body = (node.body or "").strip("\n")
        if node.mode == "spec":
            # Python literal (safe) => dict
            if not body.strip():
                return {"type": "scatter", "title": "", "scatters": [], "lines": []}
            return ast.literal_eval(body)

        # mode=python (default): execute body and read __diagram__
        # We'll provide helpers and a small allowed builtins set.
        safe_builtins = {
            "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
            "range": range, "enumerate": enumerate, "zip": zip,
            "list": list, "dict": dict, "set": set, "tuple": tuple,
            "float": float, "int": int, "str": str,
        }

        def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            # allow a small, explicit allowlist
            allow = {"math", "random"}
            root = (name or "").split(".", 1)[0]
            if root not in allow:
                raise ImportError(f"import of '{name}' not allowed in diagram sandbox")
            return __import__(name, globals, locals, fromlist, level)

        safe_builtins["__import__"] = _guarded_import
        g: Dict[str, Any] = {
            "__builtins__": safe_builtins,
            "math": math,
            "random": random,
        }
        l: Dict[str, Any] = {}

        # allow diagram authors to use the same classes as the backend
        g.update({
            "AxisSpec": AxisSpec,
            "PlotSpec": PlotSpec,
            "Style": Style,
            "ScatterSeries": ScatterSeries,
            "LineSeries": LineSeries,
            "Histogram": Histogram,
            "BoxPlot": BoxPlot,
            "unicode_plot": unicode_plot,
        })

        exec(body, g, l)  # noqa: S102 (user-controlled by design)
        if "__diagram__" in l:
            return l["__diagram__"]
        if "__diagram__" in g:
            return g["__diagram__"]
        # fallback: if user directly computed plot into variable `diagram`
        if "diagram" in l:
            return l["diagram"]
        if "diagram" in g:
            return g["diagram"]
        raise ValueError("Diagram body must define __diagram__ (or diagram).")

    @staticmethod
    def _spec_to_plot(spec: Dict[str, Any], width: int, height: int) -> str:
        typ = (spec.get("type") or "scatter").strip().lower()
        title = str(spec.get("title") or "")

        x_label = str(spec.get("x_label") or (spec.get("x") or "x"))
        y_label = str(spec.get("y_label") or (spec.get("y") or "y"))
        grid = bool(spec.get("grid", True))
        legend = bool(spec.get("legend", True))

        x_ticks = spec.get("x_ticks_values")
        if x_ticks is None and isinstance(spec.get("x_ticks"), list):
            x_ticks = spec.get("x_ticks")
        y_ticks = spec.get("y_ticks_values")
        if y_ticks is None and isinstance(spec.get("y_ticks"), list):
            y_ticks = spec.get("y_ticks")

        ps = PlotSpec(
            width=max(10, width),
            height=max(6, height),
            title=title,
            x=AxisSpec(
                label=x_label,
                log=bool(spec.get("x_log", False)),
                ticks=int(spec.get("x_ticks", 6)) if not isinstance(spec.get("x_ticks"), list) else max(2, len(spec.get("x_ticks"))),
                ticks_values=list(x_ticks) if x_ticks is not None else None,
                ticklabels=list(spec.get("x_ticklabels")) if spec.get("x_ticklabels") is not None else None,
                min_val=spec.get("xmin"),
                max_val=spec.get("xmax"),
            ),
            y=AxisSpec(
                label=y_label,
                log=bool(spec.get("y_log", False)),
                ticks=int(spec.get("y_ticks", 6)) if not isinstance(spec.get("y_ticks"), list) else max(2, len(spec.get("y_ticks"))),
                ticks_values=list(y_ticks) if y_ticks is not None else None,
                ticklabels=list(spec.get("y_ticklabels")) if spec.get("y_ticklabels") is not None else None,
                min_val=spec.get("ymin"),
                max_val=spec.get("ymax"),
            ),
            grid=grid,
            legend=legend,
        )

        if typ == "hist":
            h = spec.get("hist") or {}
            hist = Histogram(
                values=list(h.get("values", [])),
                bins=int(h.get("bins", 20)),
                name=str(h.get("name", "hist")),
                shade=str(h.get("shade", "█"))[:1] or "█",
            )
            return unicode_plot(spec=ps, hist=hist)

        if typ == "box":
            b = spec.get("box") or {}
            box = BoxPlot(
                groups=[list(g) for g in b.get("groups", [])],
                labels=list(b.get("labels")) if b.get("labels") is not None else None,
                name=str(b.get("name", "box")),
            )
            return unicode_plot(spec=ps, box=box)

        scatters: List[ScatterSeries] = []
        for s in spec.get("scatters", []) or []:
            marker = str(s.get("marker", "o"))[:1] or "o"
            scatters.append(ScatterSeries(
                x=list(s.get("x", [])),
                y=list(s.get("y", [])),
                name=str(s.get("name", "series")),
                style=Style(marker=marker),
            ))

        lines: List[LineSeries] = []
        for l in spec.get("lines", []) or []:
            ch = str(l.get("ch", "·"))[:1] or "·"
            dashed = bool(l.get("dashed", False))
            lines.append(LineSeries(
                x=list(l.get("x", [])),
                y=list(l.get("y", [])),
                name=str(l.get("name", "line")),
                style=Style(line=ch, dashed=dashed),
            ))

        if typ not in ("scatter", "lines"):
            # allow combining scatters+lines even if type is missing
            typ = "scatter" if scatters else "lines"

        return unicode_plot(spec=ps, scatters=scatters, lines=lines)

    def try_render(
        self,
        *,
        node: HostNode,
        meta: Dict[str, Any],
        compiler: Any,
        max_width: int,
    ) -> Optional[Union[HostBox, FloatItem]]:
        if not isinstance(node, PlotDiagramNode):
            return None

        # resolve diagram number (top-level meta OR pre-attached _meta in twocolumns)
        dno = meta.get("diano")
        if dno is None:
            ch_meta = getattr(node, "_meta", {})
            if isinstance(ch_meta, dict):
                dno = ch_meta.get("diano")

        width_expr = getattr(node, "width", r"\columnwidth")
        wants_span_full = isinstance(width_expr, str) and ("\\textwidth" in width_expr)

        render_width_limit = int(meta.get("_text_width", max_width)) if wants_span_full else max_width

        ctx = DimContext(
            textwidth=render_width_limit,
            columnwidth=max_width,
            textheight=10**9,
            canvaswidth=render_width_limit,
            canvasheight=10**9,
        )
        w = eval_dim(width_expr, ctx, default=min(80, render_width_limit))
        w = max(10, min(w, render_width_limit))
        # Keep the requested width invariant across every block type. The
        # frame is part of width rather than an addition outside it.
        content_width = max(8, w - 2) if node.frame else w

        h = eval_dim(getattr(node, "height", None), ctx, default=24)
        h = max(6, h)

        # Build plot or accept pre-rendered
        try:
            payload = self._load_spec_from_body(node)
            if isinstance(payload, str):
                art = payload
                title_hint = ""
            elif isinstance(payload, dict):
                art = self._spec_to_plot(payload, width=content_width, height=h)
                title_hint = str(payload.get("title") or payload.get("type") or "").strip()
            else:
                raise ValueError("__diagram__ must be a dict (spec) or str (already rendered)")
        except Exception as e:
            art = f"[diagram error: {e}]"
            title_hint = ""

        lines = art.splitlines() if art else [""]
        art_w = max((len(x) for x in lines), default=0)
        box_w = min(content_width, max(8, min(content_width, max(art_w, 8))))
        lines = [(ln[:box_w]).ljust(box_w) for ln in lines]

        # caption
        caption = node.caption
        if caption is None:
            cap_core = title_hint or "diagram"
            caption = f"Diagram {dno}: {cap_core}" if dno is not None else cap_core
        elif dno is not None:
            caption = f"Diagram {dno}: {caption}"
        caption = compiler.resolve_inline_text(caption) if hasattr(compiler, "resolve_inline_text") else (compiler.refs.resolve_text(caption) if hasattr(compiler, "refs") else caption)
        cap_line = (caption[:box_w]).ljust(box_w)

        if node.frame:
            inner_w = min(box_w, render_width_limit - 2)
            top = "┌" + "─" * inner_w + "┐"
            bot = "└" + "─" * inner_w + "┘"
            framed: List[str] = [top]
            for ln in lines:
                framed.append("│" + (ln[:inner_w]).ljust(inner_w) + "│")
            framed.append("│" + (cap_line[:inner_w]).ljust(inner_w) + "│")
            framed.append(bot)
            box_lines = framed
            box_width = inner_w + 2
        else:
            box_lines = lines + [cap_line]
            box_width = box_w

        box = HostBox.from_lines(box_lines, width=box_width)
        return FloatItem(
            box=box,
            placement=node.placement,
            meta={
                "kind": "diagram",
                "number": dno,
                "span_full": bool(wants_span_full),
            },
        )

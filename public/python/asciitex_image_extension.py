from __future__ import annotations

"""
asciitex_image_extension.py

Extension for `asciitex.py` that makes \\includeimage[...]{} render
as actual ASCII/Unicode art using Pillow, with proper float behavior in one-
and two-column layouts.

Fixes vs earlier draft:
  - caption option parsing: supports caption=..., Caption=..., quoted strings
  - two-column balancing: supports full-width (\\textwidth) spanning floats that
    occupy both columns and influence balancing, similar to the core ImageNode
    special-case.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import io
import math
import re
import urllib.request

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore


# --- host imports (from asciitex_extended_v5) ---
# We import lazily so the file can be imported even if the host changes slightly.
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
    FloatQueue,
)


# ============================================================
# Image -> ASCII pipeline (pure python + Pillow)
# ============================================================

def _load_image(path_or_url: str) -> "Image.Image":
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        with urllib.request.urlopen(path_or_url) as resp:
            data = resp.read()
        return Image.open(io.BytesIO(data))
    return Image.open(path_or_url)

def _clamp(x, lo=0.0, hi=255.0):
    return lo if x < lo else hi if x > hi else x

def _to_grayscale(img: "Image.Image") -> "Image.Image":
    return img.convert("L")

def _auto_contrast(imgL: "Image.Image", cutoff=0.01) -> "Image.Image":
    pixels = list(imgL.getdata())
    if not pixels:
        return imgL
    hist = [0] * 256
    for p in pixels:
        hist[p] += 1
    n = len(pixels)
    cut = int(n * cutoff)

    s = 0
    lo = 0
    for i, h in enumerate(hist):
        s += h
        if s > cut:
            lo = i
            break

    s = 0
    hi = 255
    for i in range(255, -1, -1):
        s += hist[i]
        if s > cut:
            hi = i
            break

    if hi <= lo:
        return imgL

    scale = 255.0 / (hi - lo)
    out = Image.new("L", imgL.size)
    out.putdata([int(_clamp((p - lo) * scale)) for p in pixels])
    return out

def _adjust_gamma(imgL: "Image.Image", gamma: float) -> "Image.Image":
    if gamma is None or abs(gamma - 1.0) < 1e-9:
        return imgL
    inv = 1.0 / gamma
    lut = [int(_clamp((i / 255.0) ** inv * 255.0)) for i in range(256)]
    return imgL.point(lut)

def _adjust_contrast(imgL: "Image.Image", contrast: float) -> "Image.Image":
    if contrast is None or abs(contrast - 1.0) < 1e-9:
        return imgL
    mid = 127.5
    lut = [int(_clamp((i - mid) * contrast + mid)) for i in range(256)]
    return imgL.point(lut)

def _resize_for_text(imgL: "Image.Image", width_chars: int, aspect: float = 0.5) -> "Image.Image":
    w, h = imgL.size
    if w == 0 or h == 0:
        return imgL
    new_w = max(1, int(width_chars))
    new_h = max(1, int(h * (new_w / w) * aspect))
    return imgL.resize((new_w, new_h), Image.Resampling.LANCZOS)

def _floyd_steinberg_dither(imgL: "Image.Image", levels: int) -> "Image.Image":
    w, h = imgL.size
    arr = [float(p) for p in imgL.getdata()]

    def quantize(v):
        if levels <= 1:
            return 0.0
        step = 255.0 / (levels - 1)
        return round(v / step) * step

    for y in range(h):
        for x in range(w):
            i = y * w + x
            old = arr[i]
            new = quantize(old)
            err = old - new
            arr[i] = new
            if x + 1 < w:
                arr[i + 1] += err * 7 / 16
            if x - 1 >= 0 and y + 1 < h:
                arr[i + w - 1] += err * 3 / 16
            if y + 1 < h:
                arr[i + w] += err * 5 / 16
            if x + 1 < w and y + 1 < h:
                arr[i + w + 1] += err * 1 / 16

    out = Image.new("L", (w, h))
    out.putdata([int(_clamp(v)) for v in arr])
    return out

def image_to_ascii(
    path_or_url: str,
    width: int = 120,
    palette: str = "classic",
    invert: bool = False,
    aspect: float = 0.45,
    autocontrast: bool = True,
    gamma: float = 1.0,
    contrast: float = 1.0,
    dither: bool = False,
) -> str:
    palettes = {
        "classic": " .:-=+*#%@",
        "minimal": " .:-=+*#",
        "blocks": " ░▒▓█",
    }
    chars = palettes.get(palette, palettes["classic"])

    img = _load_image(path_or_url)
    imgL = _to_grayscale(img)

    if autocontrast:
        imgL = _auto_contrast(imgL, cutoff=0.01)

    imgL = _adjust_gamma(imgL, gamma=gamma)
    imgL = _adjust_contrast(imgL, contrast=contrast)
    imgL = _resize_for_text(imgL, width_chars=width, aspect=aspect)

    if invert:
        imgL = Image.eval(imgL, lambda p: 255 - p)

    if dither:
        imgL = _floyd_steinberg_dither(imgL, levels=len(chars))

    px = list(imgL.getdata())
    w, h = imgL.size
    n = len(chars) - 1

    def pick(p):
        idx = int(round((p / 255.0) * n))
        return chars[idx]

    lines = []
    for y in range(h):
        row = px[y * w : (y + 1) * w]
        lines.append("".join(pick(p) for p in row))
    return "\n".join(lines)


# ============================================================
# Node definition
# ============================================================

@dataclass
class AsciiImageNode(HostNode):
    path: str
    width: DimExpr = r"\columnwidth"
    placement: str = "t"

    palette: str = "classic"
    invert: bool = False
    aspect: float = 0.45
    autocontrast: bool = True
    gamma: float = 1.0
    contrast: float = 1.0
    dither: bool = False

    caption: Optional[str] = None
    frame: bool = False


# ============================================================
# Two-column engine patch: support full-width spanning floats
# ============================================================

_ORIG_LAYOUT_TWO_COLUMNS = None

def _patch_layout_two_columns_for_fullwidth_spans() -> None:
    """
    Monkey-patch LayoutEngine.layout_two_columns so that FloatItem objects whose
    meta contains {"span_full": True} are placed across both columns.

    This makes \\includeimage[width=\\textwidth] work nicely in twocolumns:
    the float occupies both columns and affects balancing/column heights.
    """
    global _ORIG_LAYOUT_TWO_COLUMNS
    if _ORIG_LAYOUT_TWO_COLUMNS is not None:
        return  # already patched

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
        # We re-use the host implementation but intercept span_full floats by
        # splitting the stream around them and laying out segments.
        # This keeps behavior identical for all other items.
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

        # Lay out each segment with original method, then insert spanning float between segments.
        cur_cursor = cursor
        for seg_idx, seg in enumerate(segments):
            if seg:
                cur_cursor = _ORIG_LAYOUT_TWO_COLUMNS(
                    self, seg, cur_cursor, col_width, gutter, balance, line_gap, auto_height=auto_height
                )
            # if there is a span float after this segment, place it now
            for idx, span in span_items:
                if idx == seg_idx:
                    # place at current y (which is at the end of the two-column region produced so far)
                    # The original method returns a cursor with x,y at left column start and y advanced
                    # to the max of both columns.
                    full_w = col_width * 2 + gutter
                    box = span.box
                    # If box is narrower, we still place it as-is; padding is already inside the box lines.
                    if (not auto_height) and box.height > (cur_cursor.region_height - cur_cursor.y):
                        return cur_cursor
                    self._place_box(box, cur_cursor.x, cur_cursor.y, kind="float", meta=span.meta)
                    cur_cursor.y += box.height + line_gap
        return cur_cursor

    LayoutEngine.layout_two_columns = layout_two_columns_patched


# ============================================================
# Parser + numbering + rendering extension
# ============================================================

class AsciiIncludeImageExtension(ParserExtension, NumberingExtension, RenderExtension):
    """
    Parser + numbering + rendering for \\includeimage[...]{} => ASCII art float.
    """

    _cmd_includeimage = re.compile(r"^\\includeimage(\[[^\]]*\])?\{([^}]+)\}\s*$")

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
        m = self._cmd_includeimage.match(lines[i].strip())
        if not m:
            return None, i, pending_label

        opts = parser.parse_kv_opts(m.group(1))

        # be forgiving: allow Caption=... (case-insensitive)
        if "caption" not in opts:
            for k in list(opts.keys()):
                if k.strip().lower() == "caption":
                    opts["caption"] = opts[k]
                    break

        path = m.group(2).strip()

        def _get_bool(key: str, default: bool) -> bool:
            v = opts.get(key, None)
            if v is None:
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        def _get_float(key: str, default: float) -> float:
            v = opts.get(key, None)
            if v is None:
                return default
            try:
                return float(v)
            except Exception:
                return default

        width: DimExpr = opts.get("width", r"\columnwidth")

        node = AsciiImageNode(
            path=path,
            width=width,
            placement=opts.get("place", "t"),
            palette=opts.get("palette", "classic"),
            invert=_get_bool("invert", False),
            aspect=_get_float("aspect", 0.45),
            autocontrast=_get_bool("autocontrast", True),
            gamma=_get_float("gamma", 1.0),
            contrast=_get_float("contrast", 1.0),
            dither=_get_bool("dither", False),
            caption=self._strip_quotes(opts.get("caption")),
            frame=_get_bool("frame", False),
        )

        if pending_label:
            node.label = pending_label
            pending_label = None

        # ensure the layout engine patch is installed once
        _patch_layout_two_columns_for_fullwidth_spans()

        return node, i + 1, pending_label

    def try_number(self, *, node: HostNode, meta: Dict[str, Any], counters: Any, refs: Any) -> bool:
        if not isinstance(node, AsciiImageNode):
            return False
        fno = counters.next_figure()
        meta["figno"] = fno
        if getattr(node, "label", None):
            refs.register(node.label, str(fno))
        return True

    def try_render(
        self,
        *,
        node: HostNode,
        meta: Dict[str, Any],
        compiler: Any,
        max_width: int,
    ) -> Optional[Union[HostBox, FloatItem]]:
        if not isinstance(node, AsciiImageNode):
            return None

        # resolve figure number (top-level meta OR pre-attached _meta in twocolumns)
        fno = meta.get("figno")
        if fno is None:
            ch_meta = getattr(node, "_meta", {})
            if isinstance(ch_meta, dict):
                fno = ch_meta.get("figno")

        width_expr = getattr(node, "width", r"\columnwidth")
        wants_span_full = isinstance(width_expr, str) and ("\\textwidth" in width_expr)

        # heuristic for full-span width in two columns:
        # max_width is column width there, so we approximate full width = 2*col + 4
        render_width_limit = int(meta.get("_text_width", max_width)) if wants_span_full else max_width

        ctx = DimContext(
            textwidth=render_width_limit,
            columnwidth=max_width,
            textheight=10**9,
            canvaswidth=render_width_limit,
            canvasheight=10**9,
        )
        w = eval_dim(width_expr, ctx, default=max(10, min(80, render_width_limit)))
        w = max(5, min(w, render_width_limit))

        # Convert image to ascii
        if Image is None:
            lines = [f"[Pillow not available: cannot render {node.path}]"]
        else:
            try:
                art = image_to_ascii(
                    node.path,
                    width=w,
                    palette=node.palette,
                    invert=node.invert,
                    aspect=node.aspect,
                    autocontrast=node.autocontrast,
                    gamma=node.gamma,
                    contrast=node.contrast,
                    dither=node.dither,
                )
                lines = art.splitlines() if art else [""]
            except Exception as e:  # pragma: no cover
                lines = [f"[includeimage error: {e}]"]

        # Normalize to width
        art_w = min(render_width_limit, max((len(x) for x in lines), default=0))
        box_w = max(1, min(render_width_limit, max(w, art_w)))
        lines = [(ln[:box_w]).ljust(box_w) for ln in lines]

        # Caption
        caption = node.caption
        if caption is None:
            caption = f"Figure {fno}: {node.path}" if fno is not None else node.path
        elif fno is not None:
            caption = f"Figure {fno}: {caption}"
        caption = compiler.refs.resolve_text(caption) if hasattr(compiler, "refs") else caption
        cap_line = (caption[:box_w]).ljust(box_w)

        # Frame
        if node.frame:
            inner_w = max(1, min(box_w, render_width_limit))
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
                "kind": "ascii_image",
                "number": fno,
                "path": node.path,
                "span_full": bool(wants_span_full),
            },
        )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BibTeX + \\cite{...} extension for asciitex (no external deps).

Features
- Inline numeric citations: \\cite{key1,key2} -> [1,2]
- Tracks first-appearance order across the whole document (including twocolumns).
- Renders a REFERENCES section from one or more .bib files (minimal built-in BibTeX parser).

Supported BibTeX (minimal)
- Entry syntax: @type{key, field = {...}, field = "...", ...}
- Field values: {...} or "..." (nested braces are handled conservatively)
- Common fields used for formatting:
    author, title, year, journal, booktitle, publisher, organization, institution,
    volume, number, pages, address, doi, url
- Author formatting: "Last, First and Other, A." or "First Last and ..."

\\bibliographystyle{...}
- Parsed and stored, but the built-in formatter currently renders a single "unsrt-like"
  output (numeric, order of first citation). The style name is kept for forward-compat.

Usage (example):

  from asciitex import TexLikeMonospaceCompiler, ExtensionRegistry
  from asciitex_bib_extension import BibCiteExtension

  reg = ExtensionRegistry()
  reg.add(BibCiteExtension(default_style="unsrt"))
  comp = TexLikeMonospaceCompiler(registry=reg)
  print(comp.compile(src, canvas_width=100, canvas_height=None))

In your TeX-like source:

  This is discussed in \\cite{knuth1984,lamport1994}.

  \\bibliographystyle{unsrt}
  \\bibliography{refs.bib}

Notes
- If a cited key is missing from the bib file(s), it is rendered as:
    [n] MISSING BIB ENTRY: key
- If you never use \\bibliography{...}, citations will still be numbered/replaced,
  but no REFERENCES section is produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import OrderedDict
import re

# Import types from asciitex
from asciitex import (
    ParserExtension,
    RenderExtension,
    Node,
    TextNode,
    BibNode,
    Box,
    TexLikeMonospaceCompiler,
)

CITE_RE = re.compile(r"""\\cite\{([^}]+)\}""")
BIBSTYLE_RE = re.compile(r"""^\\bibliographystyle\{([^}]+)\}\s*$""")

# --- Minimal BibTeX parsing -------------------------------------------------

_ENTRY_RE = re.compile(r"@(?P<typ>[A-Za-z]+)\s*\{\s*(?P<body>.*)", re.DOTALL)

def _strip_outer_braces(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        return s[1:-1].strip()
    return s

def _strip_outer_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].strip()
    return s

def _unescape_texish(s: str) -> str:
    # very small subset: collapse whitespace and strip common brace grouping
    s = s.replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # remove grouping braces used for capitalization protection
    s = s.replace("{", "").replace("}", "")
    return s

def _read_balanced(src: str, i: int, open_ch: str, close_ch: str) -> Tuple[str, int]:
    """Read from src[i] (which should be open_ch) until matching close_ch."""
    assert i < len(src) and src[i] == open_ch
    depth = 0
    out: List[str] = []
    while i < len(src):
        ch = src[i]
        out.append(ch)
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    return "".join(out), i

def parse_bibtex_files(paths: List[str]) -> Dict[str, Dict[str, str]]:
    """
    Return dict: key -> { '__type__': type, field: value, ... }
    Later files override earlier keys.
    """
    entries: Dict[str, Dict[str, str]] = {}
    for path in paths:
        try:
            txt = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        # Remove line comments starting with % (common in BibTeX)
        txt = re.sub(r"(?m)^\s*%.*$", "", txt)

        i = 0
        while i < len(txt):
            at = txt.find("@", i)
            if at == -1:
                break
            m = _ENTRY_RE.match(txt, at)
            if not m:
                i = at + 1
                continue
            typ = m.group("typ").lower()
            # We are at "@type{"
            brace_pos = txt.find("{", at)
            if brace_pos == -1:
                i = at + 1
                continue
            block, j = _read_balanced(txt, brace_pos, "{", "}")
            body = block[1:-1].strip()

            # Split: key, rest
            if "," not in body:
                i = j
                continue
            key, rest = body.split(",", 1)
            key = key.strip()
            fields_src = rest.strip()

            field_map: Dict[str, str] = {"__type__": typ}

            k = 0
            while k < len(fields_src):
                # skip whitespace and commas
                while k < len(fields_src) and fields_src[k] in " \t\r\n,":
                    k += 1
                if k >= len(fields_src):
                    break

                # field name
                name_m = re.match(r"[A-Za-z_][A-Za-z0-9_:-]*", fields_src[k:])
                if not name_m:
                    break
                name = name_m.group(0).lower()
                k += len(name_m.group(0))

                # skip spaces and '='
                while k < len(fields_src) and fields_src[k].isspace():
                    k += 1
                if k < len(fields_src) and fields_src[k] == "=":
                    k += 1
                while k < len(fields_src) and fields_src[k].isspace():
                    k += 1
                if k >= len(fields_src):
                    break

                # value (brace / quote / bare token)
                if fields_src[k] == "{":
                    val_block, k2 = _read_balanced(fields_src, k, "{", "}")
                    val = _strip_outer_braces(val_block)
                    k = k2
                elif fields_src[k] == '"':
                    # read quoted string, naive but handles escaped quotes minimally
                    k += 1
                    buf: List[str] = []
                    while k < len(fields_src):
                        ch = fields_src[k]
                        if ch == '"' and (k == 0 or fields_src[k - 1] != "\\"):
                            k += 1
                            break
                        buf.append(ch)
                        k += 1
                    val = "".join(buf).strip()
                else:
                    # bare token until comma
                    start = k
                    while k < len(fields_src) and fields_src[k] not in ",\n\r":
                        k += 1
                    val = fields_src[start:k].strip()

                field_map[name] = _unescape_texish(val)

                # move to next comma
                while k < len(fields_src) and fields_src[k] not in ",":
                    k += 1
                if k < len(fields_src) and fields_src[k] == ",":
                    k += 1

            if key:
                entries[key] = field_map

            i = j
    return entries

def _format_authors(author_field: str) -> str:
    s = author_field.strip()
    if not s:
        return ""
    parts = [p.strip() for p in re.split(r"\s+and\s+", s, flags=re.IGNORECASE) if p.strip()]
    out: List[str] = []

    def fmt_one(p: str) -> str:
        # "Last, First" or "First Last"
        if "," in p:
            last, first = [x.strip() for x in p.split(",", 1)]
        else:
            toks = p.split()
            if len(toks) == 1:
                return toks[0]
            first, last = " ".join(toks[:-1]), toks[-1]
        # initials for first names
        inits = []
        for t in first.replace("-", " ").split():
            if t:
                inits.append(t[0].upper() + ".")
        return f"{last}, {' '.join(inits)}".strip().rstrip(",")

    for p in parts:
        out.append(fmt_one(p))
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    if len(out) == 2:
        return f"{out[0]} and {out[1]}"
    return f"{out[0]} et al."

def format_bib_entry(entry: Dict[str, str]) -> str:
    typ = entry.get("__type__", "").lower()
    author = _format_authors(entry.get("author", ""))
    title = entry.get("title", "")
    year = entry.get("year", "")
    journal = entry.get("journal", "")
    booktitle = entry.get("booktitle", "")
    publisher = entry.get("publisher", "") or entry.get("organization", "") or entry.get("institution", "")
    volume = entry.get("volume", "")
    number = entry.get("number", "")
    # BibTeX writes page ranges with a double hyphen; Unicode output uses an en dash.
    pages = re.sub(r"\s*--+\s*", "–", entry.get("pages", "").strip())
    doi = entry.get("doi", "")
    url = entry.get("url", "")

    chunks: List[str] = []
    if author:
        chunks.append(f"{author}.")
    if title:
        chunks.append(f"{title}.")
    if typ in ("article",):
        where = journal
        if where:
            chunks.append(where + ".")
        vn = ""
        if volume:
            vn += volume
        if number:
            vn += f"({number})" if vn else f"({number})"
        if vn:
            chunks.append(vn + ".")
        if pages:
            chunks.append(f"pp. {pages}.")
    elif typ in ("inproceedings", "incollection"):
        if booktitle:
            chunks.append(f"In {booktitle}.")
        if pages:
            chunks.append(f"pp. {pages}.")
        if publisher:
            chunks.append(publisher + ".")
    else:
        # book, misc, techreport, etc.
        if publisher:
            chunks.append(publisher + ".")
        if pages:
            chunks.append(f"pp. {pages}.")
    if year:
        chunks.append(str(year).strip() + ".")
    if doi:
        chunks.append(f"doi:{doi}.")
    if url:
        chunks.append(f"{url}.")

    # normalize spacing
    s = " ".join(" ".join(ch.split()) for ch in chunks).strip()
    return s

# --- Extension --------------------------------------------------------------

@dataclass
class BibStyleNode(Node):
    style: str

@dataclass
class BibCiteExtension(ParserExtension, RenderExtension):
    """
    Single extension that:
      - parses \\bibliographystyle{...}
      - replaces \\cite{...} in TextNode rendering
      - renders BibNode (\\bibliography{...}) as REFERENCES based on collected citations
    """
    default_style: str = "unsrt"

    # state (per compiler run; persists on the extension instance)
    citations: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    bib_style: str = field(default="unsrt")

    # cache for loaded bib
    _bib_entries: Optional[Dict[str, Dict[str, str]]] = field(default=None, init=False)
    _bib_paths: Optional[Tuple[str, ...]] = field(default=None, init=False)

    def reset(self) -> None:
        self.citations.clear()
        self.bib_style = self.default_style
        self._bib_entries = None
        self._bib_paths = None

    # ---------------------------
    # ParserExtension
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
        m = BIBSTYLE_RE.match(line)
        if not m:
            return None, i, pending_label
        style = m.group(1).strip()
        return BibStyleNode(style=style), i + 1, pending_label

    # ---------------------------
    # RenderExtension
    # ---------------------------
    def try_render(
        self,
        *,
        node: Node,
        meta: Dict[str, Any],
        compiler: TexLikeMonospaceCompiler,
        max_width: int,
    ) -> Optional[Union[Box, Any]]:
        if isinstance(node, BibStyleNode):
            self.bib_style = (node.style or self.default_style).strip() or self.default_style
            return Box.from_lines([], width=max_width)

        if isinstance(node, TextNode):
            txt = self._replace_cites(node.text)
            return compiler.typesetter.text(txt, max_width=max_width)

        if isinstance(node, BibNode):
            entries = self._build_reference_entries(node.bibfiles)
            return compiler.typesetter.bibliography(entries, max_width=max_width)

        return None

    # ---------------------------
    # Internal helpers
    # ---------------------------
    def _replace_cites(self, text: str) -> str:
        def repl(m: re.Match) -> str:
            keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
            nums: List[str] = []
            for k in keys:
                if k not in self.citations:
                    self.citations[k] = len(self.citations) + 1
                nums.append(str(self.citations[k]))
            return "[" + ",".join(nums) + "]"
        return CITE_RE.sub(repl, text)

    def _load_bib(self, bibfiles: List[str]) -> Dict[str, Dict[str, str]]:
        paths = tuple([p.strip() for p in bibfiles if p.strip()])
        if self._bib_entries is not None and self._bib_paths == paths:
            return self._bib_entries

        self._bib_entries = parse_bibtex_files(list(paths))
        self._bib_paths = paths
        return self._bib_entries

    def _build_reference_entries(self, bibfiles: List[str]) -> List[str]:
        if not self.citations:
            return []

        bib = self._load_bib(bibfiles)
        keys_sorted = sorted(self.citations.keys(), key=lambda k: self.citations[k])

        out: List[str] = []
        for k in keys_sorted:
            n = self.citations[k]
            ent = bib.get(k)
            if not ent:
                out.append(f"[{n}] MISSING BIB ENTRY: {k}")
                continue
            out.append(f"[{n}] {format_bib_entry(ent)}")
        return out

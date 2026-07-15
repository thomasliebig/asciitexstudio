# Extending AsciiTeX Studio

AsciiTeX Studio runs the Python AsciiTeX engine in the browser through Pyodide. New syntax is usually added as a small parser/render/numbering extension rather than by changing the whole compiler pipeline.

## Where extensions live

- Core compiler: `public/python/asciitex.py`
- Layout commands and lists: `public/python/asciitex_layout_extension.py`
- Math rendering: `public/python/asciitex_math_extension.py`
- Images: `public/python/asciitex_image_extension.py`
- Diagrams: `public/python/asciitex_diagram_extension.py`
- Tables and boxes: `public/python/asciitex_table_extension.py`
- Browser worker registration/cache: `public/pyodide-worker.js`

## Typical steps for a new command

1. Add a `Node` dataclass for the parsed command or environment.
2. Add parser logic in a `ParserExtension.try_parse(...)`.
3. Add numbering logic in `try_number(...)` if the command needs counters or labels.
4. Add rendering logic in `try_render(...)`.
5. Register the extension in `public/pyodide-worker.js`.
6. Add examples to the seed document if it is user-facing.
7. Bump engine/cache versions so browsers do not reuse stale output.
8. Add a small Python regression test and run the web build.

## Commands vs environments

Inline/simple commands like `\hr[...]` usually parse one line and return one node.

Block environments like:

```tex
\begin{box}[width=\textwidth]
...
\end{box}
```

should collect body lines until the matching end command. If the body may contain nested AsciiTeX syntax, parse it again with `parser.parse(...)` and store child nodes, as boxes do.

## Floats

A rendered block becomes a float by returning:

```python
FloatItem(box=box, placement=node.placement, meta={"kind": "figure"})
```

Supported placements are:

- `h` — place near the source position
- `t` — queue for top placement
- `b` — queue for bottom placement
- `inline` / `none` — return a normal `Box`

Use `width=\columnwidth` for column floats and `width=\textwidth` for spanning/full-width content. In two-column layout, keep floats in stream order unless there is a strong reason to flush layout manually.

## Labels, references, and counters

If a node supports labels:

1. Keep `label: Optional[str]` on the node.
2. In `try_number`, allocate the counter once.
3. Register the label with `refs.register(node.label, str(number))`.
4. Store the number in `meta`, e.g. `meta["tableno"] = number`.

Avoid numbering during rendering unless the node is strictly local and cannot be reached by the main numbering pass.

## Text width and wrapping

Always render against the `max_width` given to `try_render`. For dimensions, use:

```python
eval_dim(node.width, DimContext(...), default=max_width)
```

For prose, prefer `compiler.typesetter.text(...)` so Knuth-Plass wrapping, hyphenation, citations, and references remain consistent. For code/verbatim content, preserve spaces but wrap long lines explicitly and mark continuations.

## Browser cache/version bumps

After changing Python rendering behavior, bump:

- `ENGINE_VERSION` in `public/pyodide-worker.js`
- worker URL and project key engine in `src/compiler.ts`
- render cache path in `src/projectFs.ts`
- service worker cache name in `public/sw.js`

This prevents old rendered output from being restored from BrowserFS or the service worker.

## UI integration

If a new command should be highlighted, update `src/MonacoPane.vue`. If it belongs in the example project, update `public/seed/main.tex`.

For user-visible features, also consider:

- Does it work in `twocolumns`?
- Does it work inside `box`?
- Does it need `numbered=false`?
- Does it need `place=h/t/b/inline`?
- Does it respect `\textwidth` and `\columnwidth`?
- Does it interact correctly with `\label`, `\ref`, `\cite`, and `\bibentry`?


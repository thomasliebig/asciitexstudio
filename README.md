# AsciiTeX Studio – Browser Edition

AsciiTeX Studio develops the original [AsciiTeX](https://github.com/thomasliebig/AsciiTeX) project into a browser-based editing and compilation environment. The Python compiler and its extensions run locally in the browser through [Pyodide](https://pyodide.org/); documents and project assets are stored through BrowserFS/IndexedDB.

The web application was developed with the assistance of a coding agent. The agent helped analyse and extend the existing AsciiTeX codebase, implement the Vue interface, integrate Pyodide and BrowserFS, and build tests and browser-oriented workflows. The resulting source remains intended for human review and maintenance.

## Features

- Monaco editor with AsciiTeX syntax highlighting
- live or manual compilation to monospaced Unicode output
- project browser for `.tex`, BibTeX, images and supporting files
- two-way navigation between source and rendered output
- adjustable editor/output split and document width
- local-first project persistence in IndexedDB
- installable Progressive Web App
- images, diagrams, tables, boxes, mathematics, bibliography and cross-references
- modular TeX documents through recursive `\input{...}` and `\include{...}`
- numbered and unnumbered sections, equations, figures, diagrams, tables and boxes

## Architecture

1. Vue and Vite provide the application shell and user interface.
2. BrowserFS exposes an in-browser project filesystem backed by IndexedDB.
3. A Web Worker loads Pyodide and the Python AsciiTeX engine.
4. The worker mounts the current project, compiles `main.tex`, and returns Unicode output.
5. Monaco edits source files while the preview displays the compiler result.

All compilation happens locally in the browser. No document upload is required by the application itself.

## Development

```sh
npm install
npm run dev
```

Create a production build with:

```sh
npm run build
```

The generated site is written to `dist/`. Progressive Web App installation requires HTTPS in production; localhost is accepted during development.

For Chrome DevTools snippets that inspect BrowserFS files, cache entries, access timestamps, and cache hits, see [Cache and BrowserFS debugging](docs/CACHE_AND_BROWSERFS_DEBUGGING.md).

## Relationship to AsciiTeX

This directory contains the browser application around AsciiTeX. Python engine files served to Pyodide live in `public/python/` and mirror the corresponding compiler sources in the parent project. When changing the engine, keep both copies synchronized and increment the engine version used by the worker so browsers do not retain stale code.

See the parent project’s license and third-party notices before redistribution.

## Website and source

- Website: <https://thomasliebig.github.io/asciitexstudio/>
- Source: <https://github.com/thomasliebig/asciitexstudio>
- Imprint: <https://tapekuna.ai/#impressum>

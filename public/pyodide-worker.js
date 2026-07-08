const WORKER_BASE = new URL('./', self.location.href)
const PYODIDE_PATH = new URL('./pyodide/', WORKER_BASE).href
const ENGINE_VERSION = '10'
const ENGINE_FILES = [
  'asciitex.py',
  'asciitex_bib_extension.py',
  'asciitex_diagram_extension.py',
  'asciitex_image_extension.py',
  'asciitex_layout_extension.py',
  'asciitex_math_extension.py',
  'asciitex_table_extension.py',
]
const HYPHENATION_FILES = ['hyph-en-us.pat.txt', 'hyph-de-1996.pat.txt']

let pyodide
let initialized

function ensureDirectory(path) {
  const parts = path.split('/').filter(Boolean)
  let current = ''
  for (const part of parts) {
    current += `/${part}`
    try { pyodide.FS.mkdir(current) } catch { /* already exists */ }
  }
}

async function initialize() {
  if (initialized) return initialized
  initialized = (async () => {
    const indexURL = PYODIDE_PATH
    importScripts(`${indexURL}pyodide.js`)
    pyodide = await loadPyodide({ indexURL })
    await pyodide.loadPackage('pillow')
    ensureDirectory('/engine')
    for (const name of ENGINE_FILES) {
      const response = await fetch(new URL(`./python/${name}?v=${ENGINE_VERSION}`, WORKER_BASE), { cache: 'no-store' })
      if (!response.ok) throw new Error(`AsciiTeX engine file missing: ${name}`)
      pyodide.FS.writeFile(`/engine/${name}`, new Uint8Array(await response.arrayBuffer()))
    }
    ensureDirectory('/engine/hyphenation')
    for (const name of HYPHENATION_FILES) {
      const response = await fetch(new URL(`./hyphenation/${name}`, WORKER_BASE))
      if (!response.ok) throw new Error(`Hyphenation file missing: ${name}`)
      pyodide.FS.writeFile(`/engine/hyphenation/${name}`, new Uint8Array(await response.arrayBuffer()))
    }
  })()
  return initialized
}

self.onmessage = async ({ data }) => {
  const { id, type } = data
  try {
    await initialize()
    if (type === 'init') {
      self.postMessage({ id, type: 'ready' })
      return
    }
    if (type !== 'compile') return

    try { pyodide.FS.unmount('/project') } catch { /* not mounted */ }
    try { pyodide.FS.rmdir('/project') } catch { /* may not exist */ }
    ensureDirectory('/project')
    for (const file of data.files) {
      const target = `/project/${String(file.path).replace(/^\/+/, '')}`
      ensureDirectory(target.slice(0, target.lastIndexOf('/')))
      pyodide.FS.writeFile(target, new Uint8Array(file.bytes))
    }

    pyodide.globals.set('main_file', `/project/${data.options.mainFile.replace(/^\/+/, '')}`)
    pyodide.globals.set('canvas_width', Number(data.options.canvasWidth))
    const started = performance.now()
    const output = await pyodide.runPythonAsync(`
import os, re, sys
sys.path.insert(0, "/engine") if "/engine" not in sys.path else None
os.chdir("/project")

from asciitex import ExtensionRegistry, TexLikeMonospaceCompiler, TypesetterAdapter
from asciitex_diagram_extension import DiagramPlotExtension
from asciitex_image_extension import AsciiIncludeImageExtension
from asciitex_layout_extension import LayoutBlocksExtension
from asciitex_math_extension import AsciiMathEquationExtension
from asciitex_table_extension import AsciiTableExtension

registry = ExtensionRegistry()
registry.add(LayoutBlocksExtension())
registry.add(AsciiMathEquationExtension())
registry.add(AsciiIncludeImageExtension())
registry.add(DiagramPlotExtension())
registry.add(AsciiTableExtension())

with open(main_file, "r", encoding="utf-8") as source_file:
    source = source_file.read()

directive = re.search(r"(?m)^%\\s*!asciitex\\s+hyphenation=([A-Za-z0-9_.-]+)\\s*$", source)
hyphenation_name = directive.group(1) if directive else "hyph-en-us.pat.txt"
if hyphenation_name not in {"hyph-en-us.pat.txt", "hyph-de-1996.pat.txt"}:
    raise ValueError(f"Unsupported hyphenation file: {hyphenation_name}")
with open(f"/engine/hyphenation/{hyphenation_name}", "r", encoding="utf-8") as pattern_file:
    typesetter = TypesetterAdapter()
    typesetter.load_hyphenation_patterns_text(pattern_file.read())

# Worker directives are comments for humans and configuration for the browser host.
source = re.sub(r"(?m)^%\\s*!asciitex[^\\n]*\\n?", "", source)

compiler = TexLikeMonospaceCompiler(typesetter=typesetter, registry=registry)
compiler.compile(source, canvas_width=canvas_width, canvas_height=None)
`)
    self.postMessage({ id, type: 'result', output: String(output), duration: performance.now() - started })
  } catch (error) {
    self.postMessage({ id, type: 'error', error: error instanceof Error ? error.stack || error.message : String(error) })
  }
}

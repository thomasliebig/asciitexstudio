const WORKER_BASE = new URL('./', self.location.href)
const PYODIDE_PATH = new URL('./pyodide/', WORKER_BASE).href
const ENGINE_VERSION = '22'
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
    pyodide.globals.set('render_cache_json', String(data.renderCache || '{}'))
    const started = performance.now()
    const compiled = await pyodide.runPythonAsync(`
import hashlib, json, os, re, sys, time
sys.path.insert(0, "/engine") if "/engine" not in sys.path else None
os.chdir("/project")

from asciitex import Box, ExtensionRegistry, FloatItem, TexLikeMonospaceCompiler, TypesetterAdapter
from asciitex_diagram_extension import DiagramPlotExtension
from asciitex_image_extension import AsciiIncludeImageExtension
from asciitex_layout_extension import LayoutBlocksExtension
from asciitex_math_extension import AsciiMathEquationExtension
from asciitex_table_extension import AsciiTableExtension

registry = ExtensionRegistry()
extensions = [LayoutBlocksExtension(), AsciiMathEquationExtension(), AsciiIncludeImageExtension(), DiagramPlotExtension(), AsciiTableExtension()]
for extension in extensions:
    registry.add(extension)

# Persistent content-addressed render cache. BrowserFS owns the JSON file; the
# worker only receives and returns its contents, keeping Pyodide filesystem
# mounts simple and deterministic.
try:
    render_cache = json.loads(render_cache_json)
except Exception:
    render_cache = {}
render_cache.setdefault("version", 1)
render_cache["generation"] = int(render_cache.get("generation", 0)) + 1
render_cache.setdefault("entries", {})
generation = render_cache["generation"]
now = int(time.time())

def dependency_digest(value):
    paths = []
    if isinstance(value, str) and os.path.isfile(value):
        paths.append(value)
    elif hasattr(value, "path") and isinstance(getattr(value, "path", None), str) and os.path.isfile(value.path):
        paths.append(value.path)
    digests = []
    for path in paths:
        with open(path, "rb") as dependency:
            digests.append(hashlib.sha256(dependency.read()).hexdigest())
    return digests

def pack_rendered(value):
    if isinstance(value, Box):
        return {"kind": "box", "lines": value.lines, "width": value.width, "role": getattr(value, "_role", None)}
    if isinstance(value, FloatItem):
        return {"kind": "float", "box": pack_rendered(value.box), "placement": value.placement, "meta": value.meta}
    if value is None:
        return {"kind": "none"}
    raise TypeError(type(value).__name__)

def unpack_rendered(value):
    if value.get("kind") == "none":
        return None
    if value.get("kind") == "float":
        return FloatItem(box=unpack_rendered(value["box"]), placement=value["placement"], meta=value.get("meta", {}))
    box = Box.from_lines(value["lines"], width=int(value["width"]))
    if value.get("role"):
        box._role = value["role"]
    return box

def cached_render(namespace, payload, dependencies, build):
    serial = json.dumps({"engine": ${JSON.stringify(ENGINE_VERSION)}, "namespace": namespace, "payload": payload, "dependencies": dependencies}, sort_keys=True, default=repr)
    key = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    entry = render_cache["entries"].get(key)
    if entry is not None:
        entry["generation"] = generation
        entry["touched"] = now
        entry["hits"] = int(entry.get("hits", 0)) + 1
        return unpack_rendered(entry["value"])
    result = build()
    try:
        packed = pack_rendered(result)
    except TypeError:
        return result
    render_cache["entries"][key] = {"generation": generation, "touched": now, "hits": 0, "value": packed}
    return result

def wrap_typesetter_method(instance, method_name):
    original = getattr(instance, method_name)
    def wrapped(*args, **kwargs):
        dependencies = []
        for value in args:
            dependencies.extend(dependency_digest(value))
        for value in kwargs.values():
            dependencies.extend(dependency_digest(value))
        return cached_render("typesetter." + method_name, {"args": args, "kwargs": kwargs}, dependencies, lambda: original(*args, **kwargs))
    setattr(instance, method_name, wrapped)

with open(main_file, "r", encoding="utf-8") as source_file:
    source = source_file.read()

directive = re.search(r"(?m)^%\\s*!asciitex\\s+hyphenation=([A-Za-z0-9_.-]+)\\s*$", source)
hyphenation_name = directive.group(1) if directive else "hyph-en-us.pat.txt"
if hyphenation_name not in {"hyph-en-us.pat.txt", "hyph-de-1996.pat.txt"}:
    raise ValueError(f"Unsupported hyphenation file: {hyphenation_name}")
with open(f"/engine/hyphenation/{hyphenation_name}", "r", encoding="utf-8") as pattern_file:
    typesetter = TypesetterAdapter()
    typesetter.load_hyphenation_patterns_text(pattern_file.read())

for method_name in ("text", "section", "equation", "codeblock", "image", "diagram"):
    wrap_typesetter_method(typesetter, method_name)

for extension in extensions[1:]:
    original_try_render = extension.try_render
    extension_name = type(extension).__name__
    def make_cached_extension(original, name):
        def cached_try_render(**kwargs):
            node = kwargs.get("node")
            payload = {
                "node_type": type(node).__name__,
                "node": vars(node) if hasattr(node, "__dict__") else repr(node),
                "meta": kwargs.get("meta", {}),
                "max_width": kwargs.get("max_width"),
            }
            return cached_render("extension." + name, payload, dependency_digest(node), lambda: original(**kwargs))
        return cached_try_render
    extension.try_render = make_cached_extension(original_try_render, extension_name)

# Worker directives are comments for humans and configuration for the browser host.
source = re.sub(r"(?m)^%\\s*!asciitex[^\\n]*\\n?", "", source)

compiler = TexLikeMonospaceCompiler(typesetter=typesetter, registry=registry)
compiled_output = compiler.compile(source, canvas_width=canvas_width, canvas_height=None)

# Drop entries not touched by this document for several generations, stale
# entries older than a week, and finally cap the cache by least-recent use.
entries = render_cache["entries"]
for key, entry in list(entries.items()):
    if generation - int(entry.get("generation", 0)) > 8 or now - int(entry.get("touched", now)) > 604800:
        entries.pop(key, None)
if len(entries) > 800:
    oldest = sorted(entries, key=lambda key: (entries[key].get("generation", 0), entries[key].get("touched", 0)))
    for key in oldest[:len(entries) - 800]:
        entries.pop(key, None)
(compiled_output, json.dumps(render_cache, separators=(",", ":")))
`)
    const [output, renderCache] = compiled.toJs()
    compiled.destroy()
    self.postMessage({ id, type: 'result', output: String(output), renderCache: String(renderCache), duration: performance.now() - started })
  } catch (error) {
    self.postMessage({ id, type: 'error', error: error instanceof Error ? error.stack || error.message : String(error) })
  }
}

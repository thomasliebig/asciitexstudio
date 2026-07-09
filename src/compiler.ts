import { readRenderCache, writeRenderCache, type ProjectFile } from './projectFs'
import { buildSourceSyncMap, type SourceSyncMap } from './syncMap'

export type CompileOptions = {
  canvasWidth: number
  mainFile: string
}

type WorkerResponse =
  | { id: number; type: 'ready' }
  | { id: number; type: 'result'; output: string; duration: number; renderCache: string }
  | { id: number; type: 'error'; error: string }

export class AsciiTeXCompiler {
  private worker = new Worker(`${import.meta.env.BASE_URL}pyodide-worker.js?v=20`)
  private sequence = 0
  private pending = new Map<number, { resolve: (value: any) => void; reject: (reason: Error) => void }>()

  constructor() {
    this.worker.onmessage = ({ data }: MessageEvent<WorkerResponse>) => {
      const request = this.pending.get(data.id)
      if (!request) return
      this.pending.delete(data.id)
      if (data.type === 'error') request.reject(new Error(data.error))
      else request.resolve(data)
    }
    this.worker.onerror = event => {
      const message = event.message || 'Pyodide worker failed to start'
      for (const request of this.pending.values()) request.reject(new Error(message))
      this.pending.clear()
    }
  }

  private request(message: object): Promise<any> {
    const id = ++this.sequence
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.worker.postMessage({ ...message, id })
    })
  }

  async initialize(): Promise<void> {
    await this.request({ type: 'init' })
  }

  private async projectKey(files: ProjectFile[], options: CompileOptions): Promise<string> {
    const encoder = new TextEncoder()
    const parts: BlobPart[] = ['engine=20\n', `width=${options.canvasWidth}\n`, `main=${options.mainFile}\n`]
    for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
      parts.push(encoder.encode(`${file.path}\n${file.data.byteLength}\n`) as BlobPart, file.data as BlobPart)
    }
    const digest = await crypto.subtle.digest('SHA-256', await new Blob(parts).arrayBuffer())
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  }

  async compile(files: ProjectFile[], options: CompileOptions): Promise<{ output: string; duration: number; syncMap: SourceSyncMap }> {
    let cache: any
    try { cache = JSON.parse(await readRenderCache()) } catch { cache = {} }
    cache.version = 1
    cache.generation = Number(cache.generation || 0)
    cache.entries ||= {}
    cache.documents ||= {}
    const projectKey = await this.projectKey(files, options)
    const cachedDocument = cache.documents[projectKey]
    if (cachedDocument?.output) {
      cachedDocument.touched = Date.now()
      cachedDocument.hits = Number(cachedDocument.hits || 0) + 1
      await writeRenderCache(JSON.stringify(cache))
      return { output: cachedDocument.output, duration: 0, syncMap: buildSourceSyncMap(files, options.mainFile, cachedDocument.output) }
    }
    const payload = files.map(file => ({
      path: file.path,
      bytes: file.data,
    }))
    const result = await this.request({ type: 'compile', files: payload, options, renderCache: JSON.stringify(cache) })
    try { cache = JSON.parse(result.renderCache) } catch { /* retain the readable cache */ }
    cache.documents ||= {}
    cache.documents[projectKey] = { output: result.output, touched: Date.now(), hits: 0 }
    const staleBefore = Date.now() - 7 * 24 * 60 * 60 * 1000
    for (const [key, document] of Object.entries<any>(cache.documents)) {
      if (Number(document.touched || 0) < staleBefore) delete cache.documents[key]
    }
    const documentKeys = Object.keys(cache.documents)
    if (documentKeys.length > 12) {
      documentKeys.sort((a, b) => cache.documents[b].touched - cache.documents[a].touched)
      for (const key of documentKeys.slice(12)) delete cache.documents[key]
    }
    await writeRenderCache(JSON.stringify(cache))
    return { ...result, syncMap: buildSourceSyncMap(files, options.mainFile, result.output) }
  }
}

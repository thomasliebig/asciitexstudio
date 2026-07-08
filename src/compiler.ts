import type { ProjectFile } from './projectFs'
import { buildSourceSyncMap, type SourceSyncMap } from './syncMap'

export type CompileOptions = {
  canvasWidth: number
  mainFile: string
}

type WorkerResponse =
  | { id: number; type: 'ready' }
  | { id: number; type: 'result'; output: string; duration: number }
  | { id: number; type: 'error'; error: string }

export class AsciiTeXCompiler {
  private worker = new Worker(`${import.meta.env.BASE_URL}pyodide-worker.js?v=9`)
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

  async compile(files: ProjectFile[], options: CompileOptions): Promise<{ output: string; duration: number; syncMap: SourceSyncMap }> {
    const payload = files.map(file => ({
      path: file.path,
      bytes: file.data,
    }))
    const result = await this.request({ type: 'compile', files: payload, options })
    const main = files.find(file => file.path === options.mainFile)
    const source = main ? new TextDecoder().decode(main.data) : ''
    return { ...result, syncMap: buildSourceSyncMap(source, result.output) }
  }
}

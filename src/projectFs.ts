import * as BrowserFS from 'browserfs'

export type ProjectFile = {
  path: string
  data: Uint8Array
  text: boolean
}

type SeedFile = {
  path: string
  url: string
  text?: boolean
}

type SeedManifest = {
  files: SeedFile[]
}

const TEXT_EXTENSIONS = new Set([
  'tex', 'bib', 'txt', 'md', 'json', 'csv', 'py', 'yaml', 'yml', 'toml', 'svg',
])

const SEED_MANIFEST_URL = 'seed/manifest.json'
const RENDER_CACHE_PATH = '/.asciitex-render-cache-v10.json'

let fs: any

function call<T>(fn: (...args: any[]) => void, ...args: any[]): Promise<T> {
  return new Promise((resolve, reject) => {
    fn(...args, (error: Error | null, value: T) => error ? reject(error) : resolve(value))
  })
}

function appUrl(path: string): string {
  return `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`
}

function normalizeProjectPath(path: string): string {
  const normalized = `/${path.replace(/^\/+/, '')}`
  if (normalized.includes('..')) throw new Error(`Invalid seed path: ${path}`)
  return normalized
}

async function loadSeedManifest(): Promise<SeedManifest> {
  const response = await fetch(appUrl(SEED_MANIFEST_URL), { cache: 'no-cache' })
  if (!response.ok) throw new Error(`Unable to load ${SEED_MANIFEST_URL}: ${response.status}`)
  const manifest = await response.json() as SeedManifest
  if (!Array.isArray(manifest.files)) throw new Error('Seed manifest must contain a files array.')
  return manifest
}

async function installSeedFiles(): Promise<void> {
  const manifest = await loadSeedManifest()
  for (const file of manifest.files) {
    const projectPath = normalizeProjectPath(file.path)
    const response = await fetch(appUrl(file.url))
    if (!response.ok) throw new Error(`Unable to load seed file ${file.url}: ${response.status}`)
    if (file.text ?? isTextPath(projectPath)) {
      await writeText(projectPath, await response.text())
    } else {
      await writeBinary(projectPath, new Uint8Array(await response.arrayBuffer()))
    }
  }
}

export async function initProjectFs(): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    BrowserFS.configure({ fs: 'IndexedDB', options: { storeName: 'asciitex-studio' } }, (error: Error | null) => {
      if (error) reject(error)
      else resolve()
    })
  })
  fs = BrowserFS.BFSRequire('fs')
  const names = await call<string[]>(fs.readdir.bind(fs), '/')
  if (names.length === 0) await installSeedFiles()
  for (const name of names) {
    if (name.startsWith('.asciitex-render-cache') && `/${name}` !== RENDER_CACHE_PATH) {
      await removeFile(`/${name}`)
    }
  }
}

export function isTextPath(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return TEXT_EXTENSIONS.has(ext)
}

export async function listFiles(): Promise<ProjectFile[]> {
  const names = (await call<string[]>(fs.readdir.bind(fs), '/'))
    .filter(name => !name.startsWith('.asciitex-'))
    .sort((a, b) => a.localeCompare(b))
  return Promise.all(names.map(async name => {
    const path = `/${name}`
    const buffer = await call<any>(fs.readFile.bind(fs), path)
    return { path, data: new Uint8Array(buffer), text: isTextPath(path) }
  }))
}

export async function readRenderCache(): Promise<string> {
  try { return await readText(RENDER_CACHE_PATH) }
  catch { return '{"version":1,"generation":0,"entries":{},"documents":{}}' }
}

export async function writeRenderCache(content: string): Promise<void> {
  await writeText(RENDER_CACHE_PATH, content)
}

export async function readText(path: string): Promise<string> {
  const data = await call<any>(fs.readFile.bind(fs), path, 'utf8')
  return String(data)
}

export async function writeText(path: string, content: string): Promise<void> {
  await call<void>(fs.writeFile.bind(fs), path, content, 'utf8')
}

export async function writeBinary(path: string, content: Uint8Array): Promise<void> {
  const Buffer = BrowserFS.BFSRequire('buffer').Buffer
  await call<void>(fs.writeFile.bind(fs), path, Buffer.from(content))
}

export async function removeFile(path: string): Promise<void> {
  await call<void>(fs.unlink.bind(fs), path)
}

export async function renameFile(from: string, to: string): Promise<void> {
  await call<void>(fs.rename.bind(fs), from, to)
}

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
const RENDER_CACHE_PATH = '/.asciitex-render-cache-v21.json'

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
  const normalized = `/${path.replaceAll('\\', '/').replace(/^\/+/, '')}`
  if (normalized.split('/').includes('..')) throw new Error(`Invalid project path: ${path}`)
  return normalized
}

function parentDirectory(path: string): string {
  const normalized = normalizeProjectPath(path)
  const slash = normalized.lastIndexOf('/')
  return slash > 0 ? normalized.slice(0, slash) : '/'
}

async function exists(path: string): Promise<boolean> {
  try {
    await call<any>(fs.stat.bind(fs), path)
    return true
  } catch {
    return false
  }
}

async function ensureDirectory(path: string): Promise<void> {
  const normalized = normalizeProjectPath(path)
  if (normalized === '/') return
  const parts = normalized.split('/').filter(Boolean)
  let current = ''
  for (const part of parts) {
    current += `/${part}`
    if (!(await exists(current))) await call<void>(fs.mkdir.bind(fs), current)
  }
}

async function ensureParentDirectory(path: string): Promise<void> {
  await ensureDirectory(parentDirectory(path))
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
  async function walk(dir: string): Promise<string[]> {
    const names = await call<string[]>(fs.readdir.bind(fs), dir)
    const out: string[] = []
    for (const name of names) {
      if (name.startsWith('.asciitex-')) continue
      const path = dir === '/' ? `/${name}` : `${dir}/${name}`
      const stat = await call<any>(fs.stat.bind(fs), path)
      if (stat.isDirectory()) out.push(...await walk(path))
      else out.push(path)
    }
    return out
  }
  const paths = (await walk('/')).sort((a, b) => a.localeCompare(b))
  return Promise.all(paths.map(async path => {
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
  const normalized = normalizeProjectPath(path)
  await ensureParentDirectory(normalized)
  await call<void>(fs.writeFile.bind(fs), normalized, content, 'utf8')
}

export async function writeBinary(path: string, content: Uint8Array): Promise<void> {
  const Buffer = BrowserFS.BFSRequire('buffer').Buffer
  const normalized = normalizeProjectPath(path)
  await ensureParentDirectory(normalized)
  await call<void>(fs.writeFile.bind(fs), normalized, Buffer.from(content))
}

export async function removeFile(path: string): Promise<void> {
  await call<void>(fs.unlink.bind(fs), path)
}

export async function renameFile(from: string, to: string): Promise<void> {
  const normalizedTo = normalizeProjectPath(to)
  await ensureParentDirectory(normalizedTo)
  await call<void>(fs.rename.bind(fs), normalizeProjectPath(from), normalizedTo)
}

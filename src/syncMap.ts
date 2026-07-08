import type { ProjectFile } from './projectFs'

export type SourceLocation = { path: string; line: number }

export type SourceSyncMap = {
  outputToSource: SourceLocation[]
  sourceToOutput: Record<string, number[]>
}

type SourceLine = SourceLocation & { text: string }

function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/\\(?:begin|end)\{[^}]+\}/g, ' ')
    .replace(/\\[a-zA-Z]+/g, ' ')
    .replace(/[{}\[\]"'=,:&|┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬─═━╭╮╰╯·]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function tokens(value: string): Set<string> {
  return new Set(normalize(value).split(' ').filter(token => token.length > 1))
}

function sourceFingerprint(line: string): string {
  const section = line.match(/^\\(?:sub)*section\*?\{(.+)\}\s*$/)
  if (section) return section[1]
  const caption = line.match(/caption=["']?([^,"'\]]+)/i)
  if (caption) return caption[1]
  const title = line.match(/["']title["']\s*:\s*["']([^"']+)/i)
  if (title) return title[1]
  const boxTitle = line.match(/title=["']([^"']+)/i)
  if (boxTitle) return boxTitle[1]
  if (line.trim().startsWith('%')) return ''
  return line
}

function similarity(source: string, output: string): number {
  const a = tokens(source)
  const b = tokens(output)
  if (!a.size || !b.size) return 0
  let common = 0
  for (const token of a) if (b.has(token)) common += 1
  const containment = common / Math.min(a.size, b.size)
  const coverage = common / a.size
  return containment * 0.65 + coverage * 0.35
}

function fillNearest<T>(values: Array<T | undefined>, fallback: T): T[] {
  const result = values.slice()
  let previous: T | undefined
  for (let index = 0; index < result.length; index += 1) {
    if (result[index] !== undefined) previous = result[index]
    else if (previous !== undefined) result[index] = previous
  }
  let next: T | undefined
  for (let index = result.length - 1; index >= 0; index -= 1) {
    if (values[index] !== undefined) next = values[index]
    else if (result[index] === undefined && next !== undefined) result[index] = next
  }
  return result.map(value => value ?? fallback)
}

function resolveInclude(fromPath: string, requested: string): string {
  const name = requested.endsWith('.tex') ? requested : `${requested}.tex`
  const parts = `${fromPath.slice(0, fromPath.lastIndexOf('/') + 1)}${name}`.split('/')
  const resolved: string[] = []
  for (const part of parts) {
    if (!part || part === '.') continue
    if (part === '..') resolved.pop()
    else resolved.push(part)
  }
  return `/${resolved.join('/')}`
}

function expandedSource(files: ProjectFile[], mainPath: string): { lines: SourceLine[]; lineCounts: Record<string, number> } {
  const decoder = new TextDecoder()
  const sources = new Map(files.filter(file => file.text && file.path.endsWith('.tex'))
    .map(file => [file.path, decoder.decode(file.data)]))
  const lineCounts: Record<string, number> = {}
  for (const [path, source] of sources) lineCounts[path] = source.split(/\r?\n/).length

  const expand = (path: string, stack: Set<string>): SourceLine[] => {
    const source = sources.get(path)
    if (source === undefined || stack.has(path)) return []
    const nextStack = new Set(stack).add(path)
    const result: SourceLine[] = []
    source.split(/\r?\n/).forEach((text, line) => {
      const include = text.trim().match(/^\\(?:input|include)\{([^}]+)\}\s*$/)
      if (include) result.push(...expand(resolveInclude(path, include[1]), nextStack))
      else result.push({ path, line, text })
    })
    return result
  }
  return { lines: expand(mainPath, new Set()), lineCounts }
}

export function buildSourceSyncMap(files: ProjectFile[], mainPath: string, output: string): SourceSyncMap {
  const { lines: sourceLines, lineCounts } = expandedSource(files, mainPath)
  const outputLines = output.split('\n')
  const directOutput: Array<SourceLocation | undefined> = new Array(outputLines.length)
  const sourceToOutput: Record<string, Array<number | undefined>> = {}
  for (const [path, count] of Object.entries(lineCounts)) sourceToOutput[path] = new Array(count)

  for (let outputIndex = 0; outputIndex < outputLines.length; outputIndex += 1) {
    let best: SourceLine | undefined
    let bestScore = 0
    for (const sourceLine of sourceLines) {
      const fingerprint = sourceFingerprint(sourceLine.text)
      if (!fingerprint) continue
      const score = similarity(fingerprint, outputLines[outputIndex])
      if (score > bestScore) { bestScore = score; best = sourceLine }
    }
    if (best && bestScore >= 0.58) {
      directOutput[outputIndex] = { path: best.path, line: best.line }
      sourceToOutput[best.path][best.line] ??= outputIndex
    }
  }

  const equations = sourceLines.filter(line => /^\\begin\{(?:equation|eqnarray)\}$/.test(line.text.trim()))
  equations.forEach((sourceLine, equationIndex) => {
    const outputIndex = outputLines.findIndex(line => line.trimEnd().endsWith(`(${equationIndex + 1})`))
    if (outputIndex < 0) return
    sourceToOutput[sourceLine.path][sourceLine.line] = outputIndex
    for (let row = Math.max(0, outputIndex - 6); row <= Math.min(outputLines.length - 1, outputIndex + 1); row += 1) {
      if (outputLines[row].trim()) directOutput[row] = { path: sourceLine.path, line: sourceLine.line }
    }
  })

  const fallback = sourceLines[0] ? { path: sourceLines[0].path, line: sourceLines[0].line } : { path: mainPath, line: 0 }
  return {
    outputToSource: fillNearest(directOutput, fallback),
    sourceToOutput: Object.fromEntries(Object.entries(sourceToOutput).map(([path, values]) => [path, fillNearest(values, 0)])),
  }
}

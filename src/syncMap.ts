export type SourceSyncMap = {
  outputToSource: number[]
  sourceToOutput: number[]
}

function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/\\(?:begin|end)\{[^}]+\}/g, ' ')
    .replace(/\\[a-zA-Z]+/g, ' ')
    .replace(/[{}\[\]"'=,:&|┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬─═━╌·]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function tokens(value: string): Set<string> {
  return new Set(normalize(value).split(' ').filter(token => token.length > 1))
}

function sourceFingerprint(line: string): string {
  const section = line.match(/^\\(?:sub)*section\{(.+)\}\s*$/)
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

function fillNearest(values: number[], fallback: number): number[] {
  const result = values.slice()
  let previous = -1
  for (let index = 0; index < result.length; index += 1) {
    if (result[index] >= 0) previous = result[index]
    else if (previous >= 0) result[index] = previous
  }
  let next = -1
  for (let index = result.length - 1; index >= 0; index -= 1) {
    if (values[index] >= 0) next = values[index]
    else if (next >= 0 && (result[index] < 0 || previous < 0)) result[index] = next
  }
  return result.map(value => value >= 0 ? value : fallback)
}

export function buildSourceSyncMap(source: string, output: string): SourceSyncMap {
  const sourceLines = source.split(/\r?\n/)
  const outputLines = output.split('\n')
  const directOutput = new Array(outputLines.length).fill(-1)
  const sourceToOutput = new Array(sourceLines.length).fill(-1)

  for (let outputIndex = 0; outputIndex < outputLines.length; outputIndex += 1) {
    let bestLine = -1
    let bestScore = 0
    for (let sourceIndex = 0; sourceIndex < sourceLines.length; sourceIndex += 1) {
      const fingerprint = sourceFingerprint(sourceLines[sourceIndex])
      if (!fingerprint) continue
      const score = similarity(fingerprint, outputLines[outputIndex])
      if (score > bestScore) {
        bestScore = score
        bestLine = sourceIndex
      }
    }
    if (bestScore >= 0.58) {
      directOutput[outputIndex] = bestLine
      if (sourceToOutput[bestLine] < 0 || bestScore > 0.9) sourceToOutput[bestLine] = outputIndex
    }
  }

  // Equation renderings are symbolic; bind the numbered block and its surrounding
  // rows explicitly to the corresponding equation environment.
  const equationStarts = sourceLines
    .map((line, index) => /^\\begin\{(?:equation|eqnarray)\}$/.test(line.trim()) ? index : -1)
    .filter(index => index >= 0)
  equationStarts.forEach((sourceIndex, equationIndex) => {
    const tag = `(${equationIndex + 1})`
    const outputIndex = outputLines.findIndex(line => line.trimEnd().endsWith(tag))
    if (outputIndex < 0) return
    sourceToOutput[sourceIndex] = outputIndex
    for (let row = Math.max(0, outputIndex - 5); row <= Math.min(outputLines.length - 1, outputIndex + 1); row += 1) {
      if (outputLines[row].trim()) directOutput[row] = sourceIndex
    }
  })

  const outputToSource = fillNearest(directOutput, 0)
  const completedSource = fillNearest(sourceToOutput, 0)
  return { outputToSource, sourceToOutput: completedSource }
}

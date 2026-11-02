export interface ParsedTable {
  columns: string[]
  rows: string[][]
  truncated: boolean
}

const ROW_LIMIT = 5000

export function parseDelimited(text: string): ParsedTable {
  const source = text.replace(/^\uFEFF/, "")
  const lines = splitLines(source)
  if (lines.length === 0) throw new Error("文件是空的。")
  const delimiter = detectDelimiter(lines[0])
  const table = lines.map((line) => splitRow(line, delimiter))
  const width = Math.max(...table.map((row) => row.length))
  const header = table[0].map((cell, index) => cell.trim() || `列${index + 1}`)
  while (header.length < width) header.push(`列${header.length + 1}`)
  const body = table.slice(1).filter((row) => row.some((cell) => cell.trim()))
  const truncated = body.length > ROW_LIMIT
  const rows = (truncated ? body.slice(0, ROW_LIMIT) : body).map((row) => {
    const next = row.map((cell) => cell.trim())
    while (next.length < header.length) next.push("")
    return next.slice(0, header.length)
  })
  if (rows.length === 0) throw new Error("除表头外没有数据行。")
  return { columns: header, rows, truncated }
}

function splitLines(text: string): string[] {
  const lines: string[] = []
  let current = ""
  let quoted = false
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (char === '"') {
      quoted = !quoted
      current += char
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[index + 1] === "\n") index += 1
      if (current.trim()) lines.push(current)
      current = ""
    } else {
      current += char
    }
  }
  if (current.trim()) lines.push(current)
  return lines
}

function detectDelimiter(line: string): string {
  const counts = [
    { mark: ",", count: splitRow(line, ",").length },
    { mark: "\t", count: splitRow(line, "\t").length },
    { mark: ";", count: splitRow(line, ";").length },
  ]
  counts.sort((a, b) => b.count - a.count)
  return counts[0].count > 1 ? counts[0].mark : ","
}

function splitRow(line: string, delimiter: string): string[] {
  const cells: string[] = []
  let current = ""
  let quoted = false
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"'
        index += 1
      } else {
        quoted = !quoted
      }
    } else if (char === delimiter && !quoted) {
      cells.push(current)
      current = ""
    } else {
      current += char
    }
  }
  cells.push(current)
  return cells
}

export async function parseWorkbook(buffer: ArrayBuffer): Promise<ParsedTable> {
  const files = await unzip(buffer)
  const shared = files.get("xl/sharedStrings.xml")
  const strings = shared ? readSharedStrings(decodeXml(shared)) : []
  const sheetName = [...files.keys()].find((name) => /^xl\/worksheets\/sheet\d+\.xml$/.test(name))
  const sheet = sheetName ? files.get(sheetName) : undefined
  if (!sheet) throw new Error("没有读到工作表。请改用 CSV。")
  return sheetToTable(decodeXml(sheet), strings)
}

function decodeXml(bytes: Uint8Array): string {
  return new TextDecoder().decode(bytes)
}

async function unzip(buffer: ArrayBuffer): Promise<Map<string, Uint8Array>> {
  const bytes = new Uint8Array(buffer)
  const view = new DataView(buffer)
  const files = new Map<string, Uint8Array>()
  let offset = 0
  while (offset + 30 <= bytes.length) {
    if (view.getUint32(offset, true) !== 0x04034b50) break
    const flags = view.getUint16(offset + 6, true)
    const method = view.getUint16(offset + 8, true)
    const compressedSize = view.getUint32(offset + 18, true)
    const nameLength = view.getUint16(offset + 26, true)
    const extraLength = view.getUint16(offset + 28, true)
    const nameStart = offset + 30
    const name = new TextDecoder().decode(bytes.subarray(nameStart, nameStart + nameLength))
    const dataStart = nameStart + nameLength + extraLength
    if (flags & 0x08 || compressedSize === 0) throw new Error("这个 Excel 使用了延后写入的压缩尺寸，请另存为 CSV。")
    const compressed = bytes.subarray(dataStart, dataStart + compressedSize)
    const raw = method === 0 ? compressed : method === 8 ? await inflate(compressed) : null
    if (!raw) throw new Error("不支持该 Excel 的压缩方式，请另存为 CSV。")
    files.set(name, raw)
    offset = dataStart + compressedSize
  }
  if (files.size === 0) throw new Error("不是可读取的 xlsx 文件。")
  return files
}

async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === "undefined") throw new Error("当前浏览器不能解压 xlsx，请改用 CSV。")
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  const stream = new Blob([copy.buffer]).stream().pipeThrough(new DecompressionStream("deflate-raw"))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

function readSharedStrings(xml: string): string[] {
  const values: string[] = []
  const items = xml.match(/<si[\s>][\s\S]*?<\/si>/g) ?? []
  for (const item of items) {
    const texts = [...item.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((match) => decodeEntities(match[1]))
    values.push(texts.join(""))
  }
  return values
}

function sheetToTable(xml: string, strings: string[]): ParsedTable {
  const rowsXml = xml.match(/<row[\s>][\s\S]*?<\/row>/g) ?? []
  const matrix: string[][] = []
  let width = 0
  for (const rowXml of rowsXml) {
    const row: string[] = []
    const cells = rowXml.match(/<c[\s>][\s\S]*?<\/c>|<c[^>]*\/>/g) ?? []
    for (const cell of cells) {
      const ref = cell.match(/\br="([A-Z]+)\d+"/)
      const index = ref ? columnIndex(ref[1]) : row.length
      const type = cell.match(/\bt="([^"]+)"/)?.[1]
      const inline = cell.match(/<t[^>]*>([\s\S]*?)<\/t>/)
      const value = cell.match(/<v>([\s\S]*?)<\/v>/)
      let text = ""
      if (type === "s" && value) text = strings[Number(value[1])] ?? ""
      else if (type === "inlineStr" && inline) text = decodeEntities(inline[1])
      else if (value) text = decodeEntities(value[1])
      row[index] = text
      width = Math.max(width, index + 1)
    }
    if (row.some((cell) => cell && cell.trim())) matrix.push(row)
    if (matrix.length > ROW_LIMIT + 1) break
  }
  if (matrix.length === 0) throw new Error("工作表是空的。")
  const header = Array.from({ length: width }, (_, index) => (matrix[0][index] || "").trim() || `列${index + 1}`)
  const body = matrix.slice(1)
  const truncated = body.length > ROW_LIMIT
  const rows = (truncated ? body.slice(0, ROW_LIMIT) : body).map((row) => header.map((_, index) => (row[index] || "").trim()))
  if (rows.length === 0) throw new Error("除表头外没有数据行。")
  return { columns: header, rows, truncated }
}

function columnIndex(letters: string): number {
  let value = 0
  for (const char of letters) value = value * 26 + (char.charCodeAt(0) - 64)
  return value - 1
}

function decodeEntities(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
}

export async function parseUpload(file: File): Promise<ParsedTable> {
  const name = file.name.toLowerCase()
  if (name.endsWith(".csv") || name.endsWith(".txt") || file.type.includes("csv") || file.type.startsWith("text/")) {
    return parseDelimited(await file.text())
  }
  if (name.endsWith(".xlsx")) return parseWorkbook(await file.arrayBuffer())
  throw new Error("当前前端可以解析 CSV 和 xlsx。请换一种文件。")
}

/** P1 前端轻量化交互静态验收：确认默认路径不再挂载完整表格加载。 */
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const region = readFileSync(resolve(root, "frontend/src/diagnosis/l1/RegionWorkspace.tsx"), "utf8")
const panel = readFileSync(resolve(root, "frontend/src/diagnosis/l1/PipelinePanel.tsx"), "utf8")

check(
  "1 P1 默认只请求区域摘要 API",
  region.includes("listAppliedRegions(") && region.includes("suggestRegions(") && region.includes("listAppliedRegions(datasetId"),
)

check(
  "2 进入 P1 时 PipelinePanel 不再自动请求 raw-rows",
  panel.includes('stageId !== "p0"') && panel.includes("listRawRows(selectedId") && /stageId !== "p0"[\s\S]*listRawRows\(selectedId/.test(panel) === false
    ? panel.includes('if (stageId !== "p0"')
    : panel.includes('stageId !== "p0" || !selectedId'),
)

check(
  "3 原始表格仅在 inspect 模式请求",
  region.includes('mode !== "inspect"') && region.includes("listRawRows(datasetId") && region.includes('mode === "inspect"'),
)

check(
  "4 默认界面包含摘要、工作表列表与异常区",
  region.includes("l1-summary-card") && region.includes("l1-sheet-panel") && region.includes("l1-exception-panel") && region.includes("识别摘要"),
)

check(
  "5 手动调整与放弃调整存在，且无逐区域确认勾选",
  region.includes("手动调整") && region.includes("放弃调整") && region.includes("应用调整") && !region.includes("确认保留此区域") && !region.includes("needs_confirmation"),
)

const regionMount = panel.slice(panel.indexOf("<RegionWorkspace"), panel.indexOf("/>", panel.indexOf("<RegionWorkspace")) + 2)
check(
  "6 RegionWorkspace 不再接收父级 page/columns 全表 props",
  regionMount.includes("datasetId={selectedId}") && regionMount.includes("sheets={sheets}") && regionMount.includes("onApplied=") && !regionMount.includes("page=") && !regionMount.includes("columns="),
)

check(
  "7 区域记录与 raw-rows 均按需触发",
  region.includes("listRegionRows(inspectRegionId") && region.includes('mode !== "inspect" || !inspectRegionId'),
)

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)

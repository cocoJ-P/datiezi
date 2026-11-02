"""L1 P0：原始文件存档、CSV/XLSX 单元格索引和分页。

使用独立临时目录，不修改官方开发集。
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p0-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(32 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app

OFFICIAL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes, **params: str):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename, **params},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def cell_map(rows: list[dict]) -> dict[tuple[int, int], dict]:
    found = {}
    for row in rows:
        for cell in row["cells"]:
            found[(row["row_number"], cell["column_number"])] = cell
    return found


def make_workbook() -> bytes:
    book = Workbook()
    visible = book.active
    visible.title = "电机"
    visible["A1"] = "0012"
    visible["B1"] = "振动"
    visible.merge_cells("A1:B1")
    visible["A2"] = 1498.6
    visible["C2"] = "=1+1"
    visible["A3"] = "隐藏内容"
    visible["B3"] = 0
    visible.row_dimensions[3].hidden = True
    hidden = book.create_sheet("隐藏表")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "隐藏表值"
    book.create_sheet("空表")
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    payload = buffer.getvalue()
    patched = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(patched, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                text = text.replace("<f>1+1</f><v></v>", "<f>1+1</f><v>2</v>")
                data = text.encode("utf-8")
            target.writestr(info, data)
    return patched.getvalue()


def main() -> None:
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    with TestClient(app) as client:
        pipeline = client.get("/api/l1/pipeline").json()
        check(
            "P0 至 P8 均已接入且默认未执行",
            pipeline["template_id"] == "steel_equipment_diagnosis_v1"
            and pipeline["stages"][0]["implemented"] is True
            and pipeline["stages"][1]["implemented"] is True
            and pipeline["stages"][2]["stage_id"] == "p2"
            and pipeline["stages"][2]["implemented"] is True
            and pipeline["stages"][3]["stage_id"] == "p3"
            and pipeline["stages"][3]["implemented"] is True
            and pipeline["stages"][3]["status"] == "not_started"
            and pipeline["stages"][4]["stage_id"] == "p4"
            and pipeline["stages"][4]["implemented"] is True
            and pipeline["stages"][4]["status"] == "not_started"
            and all(item["implemented"] is True for item in pipeline["stages"][5:])
            and all(item["status"] in {"not_started", "pending"} for item in pipeline["stages"][5:])
            and pipeline["stages"][5]["stage_id"] == "p5"
            and pipeline["stages"][5]["depends_on"] == "p4"
            and pipeline["stages"][8]["stage_id"] == "p8",
        )

        standard = "设备,备注\n主电机,正常运行\n".encode("utf-8")
        standard_response = upload(client, "中文样本.csv", standard)
        standard_body = standard_response.json()
        standard_id = standard_body["dataset_id"]
        rows = client.get(f"/api/l1/datasets/{standard_id}/raw-rows", params={"limit": 20}).json()
        mapped = cell_map(rows["rows"])
        stored = (TMP / "data" / "files" / f"{standard_id}.csv").read_bytes()
        check(
            "中文 CSV 原件和单元格",
            standard_response.status_code == 201
            and standard_body["parse_status"] == "parsed"
            and standard_body["sha256"] == sha256(standard)
            and stored == standard
            and mapped[(1, 1)]["raw_value"] == "设备"
            and mapped[(2, 1)]["raw_value"] == "主电机"
            and mapped[(2, 2)]["raw_value"] == "正常运行"
            and mapped[(1, 1)]["cell_type"] == "text",
        )

        special = '编号,数值,备注\n0012,0,NA\n,"","   "\n"第一行\n第二行",0,保留\n短行,只有两列\n'.encode("utf-8")
        special_response = upload(client, "特殊字段.csv", special)
        special_body = special_response.json()
        special_rows = client.get(f"/api/l1/datasets/{special_body['dataset_id']}/raw-rows", params={"limit": 20}).json()
        special_map = cell_map(special_rows["rows"])
        multiline = next(item for item in special_rows["rows"] if item["row_number"] == 4)
        short = next(item for item in special_rows["rows"] if item["row_number"] == 5)
        check(
            "前导零、零值、空字段和多行字段",
            special_map[(2, 1)]["raw_value"] == "0012"
            and special_map[(2, 2)]["raw_value"] == "0"
            and special_map[(2, 3)]["raw_value"] == "NA"
            and special_map[(3, 1)]["raw_value"] == ""
            and special_map[(3, 2)]["raw_value"] == ""
            and special_map[(3, 3)]["raw_value"] == "   "
            and multiline["source_line_start"] == 4
            and multiline["source_line_end"] == 5
            and special_map[(4, 1)]["raw_value"] == "第一行\n第二行"
            and short["parse_status"] == "column_count_mismatch"
            and [cell["column_number"] for cell in short["cells"]] == [1, 2]
            and special_body["parse_status"] == "parsed_with_errors",
            special_body["parse_status"],
        )

        duplicated = "备注,备注\n甲,乙\n".encode("utf-8")
        duplicated_body = upload(client, "重复列名.csv", duplicated).json()
        duplicated_rows = client.get(f"/api/l1/datasets/{duplicated_body['dataset_id']}/raw-rows").json()
        duplicated_map = cell_map(duplicated_rows["rows"])
        check(
            "重复列名按位置保留",
            duplicated_map[(1, 1)]["raw_value"] == "备注"
            and duplicated_map[(1, 2)]["raw_value"] == "备注"
            and duplicated_map[(1, 1)]["column_id"] != duplicated_map[(1, 2)]["column_id"]
            and duplicated_map[(2, 2)]["raw_value"] == "乙",
        )

        broken = upload(client, "坏引号.csv", '编号,备注\n"没有闭合,仍然保留\n'.encode("utf-8"))
        broken_body = broken.json()
        broken_rows = client.get(f"/api/l1/datasets/{broken_body['dataset_id']}/raw-rows")
        broken_file = (TMP / "data" / "files" / f"{broken_body['dataset_id']}.csv").read_bytes()
        check(
            "非法引号失败但保留原件",
            broken.status_code == 201
            and broken_body["parse_status"] == "failed"
            and broken_body["p0_status"] == "failed"
            and broken_body["error"]["error_type"] == "unmatched_quote"
            and broken_rows.status_code == 409
            and broken_file == '编号,备注\n"没有闭合,仍然保留\n'.encode("utf-8"),
        )

        header_only = upload(client, "只有表头.csv", "只有表头\n".encode("utf-8")).json()
        header_rows = client.get(f"/api/l1/datasets/{header_only['dataset_id']}/raw-rows").json()
        check(
            "只有一行时不报完整失败",
            header_only["parse_status"] == "parsed" and header_rows["total_rows"] == 1 and header_rows["rows"][0]["cells"][0]["raw_value"] == "只有表头",
        )

        workbook = make_workbook()
        xlsx_response = upload(client, "多工作表.xlsx", workbook)
        xlsx_body = xlsx_response.json()
        sheets = client.get(f"/api/l1/datasets/{xlsx_body['dataset_id']}/sheets").json()["sheets"]
        motor = next(item for item in sheets if item["name"] == "电机")
        xlsx_rows = client.get(
            f"/api/l1/datasets/{xlsx_body['dataset_id']}/raw-rows",
            params={"sheet_id": motor["sheet_id"], "limit": 20},
        ).json()
        xlsx_map = cell_map(xlsx_rows["rows"])
        formula = xlsx_map[(2, 3)]
        formula_detail = client.get(f"/api/l1/datasets/{xlsx_body['dataset_id']}/raw-cells/{formula['cell_id']}").json()
        xlsx_file = client.get(f"/api/l1/datasets/{xlsx_body['dataset_id']}/file")
        check(
            "XLSX 工作表、公式、合并和隐藏行",
            xlsx_response.status_code == 201
            and [item["name"] for item in sheets] == ["电机", "隐藏表", "空表"]
            and next(item for item in sheets if item["name"] == "隐藏表")["visibility"] == "hidden"
            and next(item for item in sheets if item["name"] == "空表")["cell_count"] == 0
            and "A1:B1" in motor["merges"]
            and xlsx_map[(1, 1)]["raw_value"] == "0012"
            and xlsx_map[(1, 1)]["merge_range"] == "A1:B1"
            and xlsx_map[(2, 1)]["raw_value"] == "1498.6"
            and xlsx_map[(2, 1)]["cell_type"] == "number"
            and formula["formula"] == "=1+1"
            and formula["cached_value"] == "2"
            and formula_detail["sheet_name"] == "电机"
            and formula_detail["coordinate"] == "C2"
            and xlsx_rows["rows"][2]["hidden"] is True
            and xlsx_map[(3, 1)]["raw_value"] == "隐藏内容"
            and xlsx_map[(3, 2)]["raw_value"] == "0"
            and xlsx_file.content.startswith(b"PK")
            and sha256(xlsx_file.content) == xlsx_body["sha256"],
            f"status={xlsx_body.get('parse_status')} formula={formula.get('formula')} cache={formula.get('cached_value')}",
        )

        rejected = upload(client, "旧版.xls", b"not-xls")
        check("旧版 xls 明确拒绝", rejected.status_code == 415 and rejected.json()["error_type"] == "unsupported_xls")
        duplicate = upload(client, "另一个名字.csv", standard)
        check(
            "相同哈希不新增记录",
            duplicate.status_code == 200
            and duplicate.json()["duplicate"] is True
            and duplicate.json()["dataset_id"] == standard_id
            and len(client.get("/api/l1/datasets").json()["datasets"]) >= 1,
        )

        bulk_rows = [f"{index},设备{index},1498.6" for index in range(1, 10001)]
        bulk = ("编号,设备,振动\n" + "\n".join(bulk_rows) + "\n").encode("utf-8")
        bulk_body = upload(client, "一万行.csv", bulk).json()
        page_a = client.get(f"/api/l1/datasets/{bulk_body['dataset_id']}/raw-rows", params={"offset": 0, "limit": 100}).json()
        page_b = client.get(f"/api/l1/datasets/{bulk_body['dataset_id']}/raw-rows", params={"offset": 100, "limit": 100}).json()
        jumped = client.get(f"/api/l1/datasets/{bulk_body['dataset_id']}/raw-rows", params={"row": 250, "limit": 100}).json()
        too_many = client.get(f"/api/l1/datasets/{bulk_body['dataset_id']}/raw-rows", params={"limit": 10000})
        check(
            "一万行分页",
            bulk_body["row_count"] == 10001
            and page_a["rows"][0]["row_number"] == 1
            and page_b["rows"][0]["row_number"] == 101
            and page_a["rows"][-1]["row_number"] != page_b["rows"][0]["row_number"]
            and jumped["offset"] == 200
            and any(item["row_number"] == 250 for item in jumped["rows"])
            and too_many.status_code == 422,
            f"rows={bulk_body.get('row_count')}",
        )

        huge_rows = [f"{index},尾部{index}" for index in range(1, 100001)]
        huge = ("编号,备注\n" + "\n".join(huge_rows) + "\n").encode("utf-8")
        huge_body = upload(client, "十万行.csv", huge).json()
        huge_head = client.get(f"/api/l1/datasets/{huge_body['dataset_id']}/raw-rows", params={"limit": 100}).json()
        huge_tail = client.get(
            f"/api/l1/datasets/{huge_body['dataset_id']}/raw-rows",
            params={"offset": 100000, "limit": 100},
        ).json()
        check(
            "十万行分页",
            huge_body["parse_status"] == "parsed"
            and huge_body["row_count"] == 100001
            and huge_head["total_rows"] == 100001
            and len(huge_head["rows"]) == 100
            and huge_tail["rows"][-1]["row_number"] == 100001
            and huge_tail["rows"][-1]["cells"][1]["raw_value"] == "尾部100000",
            f"rows={huge_body.get('row_count')}",
        )
        persisted = standard_id

    with TestClient(app) as restarted:
        again = restarted.get(f"/api/l1/datasets/{persisted}").json()
        again_rows = restarted.get(f"/api/l1/datasets/{persisted}/raw-rows", params={"limit": 5}).json()
        check(
            "重启后仍能读取原件",
            again["sha256"] == sha256(standard)
            and again["storage_consistent"] is True
            and again_rows["rows"][1]["cells"][0]["raw_value"] == "主电机",
        )

    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after and official_before != "")
    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

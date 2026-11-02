"""L1 P2：设备分组索引。使用临时目录，不修改官方开发集。"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p2-"))
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
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def apply_p1(client: TestClient, dataset_id: str):
    suggestions = client.get(f"/api/l1/datasets/{dataset_id}/regions/suggestions").json()
    regions = []
    for item in suggestions["regions"]:
        copied = dict(item)
        if copied.get("confirmation_status") == "needs_confirmation":
            copied["confirmed"] = True
        regions.append(copied)
    response = client.post(
        f"/api/l1/datasets/{dataset_id}/regions/apply",
        json={
            "sheets": [{"sheet_id": item["sheet_id"], "enabled": True} for item in suggestions["sheet_settings"]],
            "regions": regions,
        },
    )
    return response


def balanced(body: dict) -> bool:
    balance = body.get("balance") or {}
    return bool(balance.get("ok")) and balance.get("grouped_count", -1) + balance.get("ungrouped_count", -2) == balance.get("business_count")


def field_of(row: dict, header: str) -> dict | None:
    for item in row["fields"]:
        if item["header"] == header:
            return item
    return None


def workbook() -> bytes:
    book = Workbook()
    vibration = book.active
    vibration.title = "振动"
    vibration.append(["设备编号", "振动"])
    vibration.append(["MM-01", 1])
    vibration.append(["MM-02", 2])
    temperature = book.create_sheet("温度")
    temperature.append(["温度", "设备ID"])
    temperature.append([40, "MM-01"])
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def ambiguous_workbook() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "歧义"
    sheet.append(["设备编号", "设备ID", "振动"])
    sheet.append(["MM-01", "ASSET-9", 1])
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def main() -> None:
    print("开始 P2 测试", flush=True)
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    with TestClient(app) as client:
        single = upload(client, "单设备.csv", "设备编号,振动\nMM-01,0.1\n".encode("utf-8"))
        single_id = single.json()["dataset_id"]
        single_p1 = apply_p1(client, single_id)
        single_fields = client.get(f"/api/l1/datasets/{single_id}/equipment-fields").json()
        single_apply = client.post(f"/api/l1/datasets/{single_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
        single_body = single_apply.json()
        single_rows = client.get(f"/api/l1/equipment-groups/{single_body['groups'][0]['group_id']}/rows").json()
        check(
            "单表单设备",
            single.status_code == 201
            and single_p1.status_code == 201
            and single_fields["regions"][0]["suggested_column_id"]
            and single_fields["regions"][0]["candidates"][0]["header"] == "设备编号"
            and single_apply.status_code == 201
            and balanced(single_body)
            and single_body["balance"]["equipment_count"] == 1
            and single_body["balance"]["grouped_count"] == 1
            and single_rows["rows"][0]["original_equipment_value"] == "MM-01"
            and single_rows["rows"][0]["source_row_id"]
            and single_rows["rows"][0]["region_id"]
            and field_of(single_rows["rows"][0], "振动")["raw_value"] == "0.1"
            and single_rows["rows"][0]["asset_id"] is None,
        )

        multi = upload(client, "多设备.csv", "设备编号,振动\nMM-01,1\nMM-02,2\n".encode("utf-8"))
        multi_id = multi.json()["dataset_id"]
        apply_p1(client, multi_id)
        multi_apply = client.post(f"/api/l1/datasets/{multi_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        check(
            "单表多设备",
            balanced(multi_apply) and multi_apply["balance"]["equipment_count"] == 2 and multi_apply["balance"]["grouped_count"] == 2,
        )

        zeros = upload(client, "前导零.csv", "设备编号,振动\n0012,0\n".encode("utf-8"))
        zero_id = zeros.json()["dataset_id"]
        apply_p1(client, zero_id)
        zero_apply = client.post(f"/api/l1/datasets/{zero_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        zero_rows = client.get(f"/api/l1/equipment-groups/{zero_apply['groups'][0]['group_id']}/rows").json()
        zero_cell = field_of(zero_rows["rows"][0], "设备编号")
        check(
            "前导零保持文本",
            zero_apply["groups"][0]["equipment_id"] == "0012"
            and zero_rows["rows"][0]["original_equipment_value"] == "0012"
            and zero_cell["raw_value"] == "0012"
            and zero_cell["cell_type"] == "text",
        )

        spaced = upload(client, "空格与大小写.csv", '设备编号,振动\n" MM-01 ",1\nMM-01,2\nmm-01,3\n'.encode("utf-8"))
        spaced_id = spaced.json()["dataset_id"]
        apply_p1(client, spaced_id)
        exact = client.post(f"/api/l1/datasets/{spaced_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        folded = client.post(
            f"/api/l1/datasets/{spaced_id}/grouping/preview",
            json={"use_suggestions": True, "cross_sheet": "separate", "match_mode": "trim_casefold"},
        ).json()
        originals = sorted(value for group in folded["groups"] for value in group["original_values"])
        check(
            "空格和大小写默认不合并",
            balanced(exact)
            and exact["balance"]["equipment_count"] == 3
            and any(group["equipment_id"] == " MM-01 " for group in exact["groups"])
            and folded["persisted"] is False
            and folded["balance"]["equipment_count"] == 1
            and originals == [" MM-01 ", "MM-01", "mm-01"],
        )

        blank = upload(client, "空编号.csv", "设备编号,振动\nMM-01,1\n,2\n无,3\n".encode("utf-8"))
        blank_id = blank.json()["dataset_id"]
        apply_p1(client, blank_id)
        blank_apply = client.post(f"/api/l1/datasets/{blank_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        ungrouped = client.get(f"/api/l1/datasets/{blank_id}/ungrouped-rows", params={"limit": 20}).json()
        reasons = {row["ungroup_reason"] for row in ungrouped["rows"]}
        check(
            "空编号和占位符进入未归组",
            balanced(blank_apply)
            and blank_apply["balance"]["grouped_count"] == 1
            and blank_apply["balance"]["ungrouped_count"] == 2
            and reasons == {"empty", "invalid"}
            and all(row["source_row_id"] and row["original_equipment_value"] in {"", "无"} for row in ungrouped["rows"]),
        )

        missing = upload(client, "无设备字段.csv", "温度,振动\n40,1\n41,2\n".encode("utf-8"))
        missing_id = missing.json()["dataset_id"]
        apply_p1(client, missing_id)
        missing_fields = client.get(f"/api/l1/datasets/{missing_id}/equipment-fields").json()
        missing_apply = client.post(f"/api/l1/datasets/{missing_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        check(
            "没有设备字段时不生成编号",
            missing_fields["regions"][0]["confirmation_status"] == "no_field"
            and missing_fields["regions"][0]["suggested_column_id"] is None
            and balanced(missing_apply)
            and missing_apply["balance"]["equipment_count"] == 0
            and missing_apply["balance"]["ungrouped_count"] == missing_apply["balance"]["business_count"]
            and missing_apply["groups"] == [],
        )

        payload = workbook()
        digest = sha256(payload)
        sheets = upload(client, "多工作表.xlsx", payload)
        sheet_id = sheets.json()["dataset_id"]
        stored = (TMP / "data" / "files" / f"{sheet_id}.xlsx").read_bytes()
        apply_p1(client, sheet_id)
        fields = client.get(f"/api/l1/datasets/{sheet_id}/equipment-fields").json()
        auto_default = client.post(f"/api/l1/datasets/{sheet_id}/grouping/apply", json={"use_suggestions": True})
        separate = client.post(f"/api/l1/datasets/{sheet_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"}).json()
        summary = client.get(f"/api/l1/datasets/{sheet_id}/grouping/summary").json()
        merged = client.post(f"/api/l1/datasets/{sheet_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "same_text"}).json()
        merged_group = next(item for item in merged["groups"] if item["equipment_id"] == "MM-01" and item["record_count"] == 2)
        merged_rows = client.get(f"/api/l1/equipment-groups/{merged_group['group_id']}/rows", params={"limit": 20}).json()
        headers = {row["sheet_name"]: [item["header"] for item in row["fields"]] for row in merged_rows["rows"]}
        temperature = next(row for row in merged_rows["rows"] if row["sheet_name"] == "温度")
        auto_body = auto_default.json()
        check(
            "跨表同号默认隔离且不阻断",
            fields["cross_sheet_decision_required"] is False
            and fields["default_cross_sheet"] == "separate"
            and auto_default.status_code == 201
            and balanced(auto_body)
            and auto_body["status"] in {"completed", "completed_with_warnings"}
            and any(item.get("code") == "potential_cross_sheet_match" for item in (auto_body.get("warnings") or []))
            and balanced(separate)
            and separate["balance"]["equipment_count"] == 3
            and len({item["sheet_id"] for item in separate["groups"] if item["equipment_id"] == "MM-01"}) == 2
            and all(item["asset_id"] is None for item in separate["groups"])
            and summary["applied"] is True
            and summary["summary"]["equipment_count"] == separate["balance"]["equipment_count"]
            and balanced(merged)
            and merged_group["scope"] == "dataset"
            and headers["振动"] == ["设备编号", "振动"]
            and headers["温度"] == ["温度", "设备ID"]
            and field_of(temperature, "温度")["raw_value"] == "40"
            and temperature["source_row_id"]
            and temperature["region_id"],
            auto_default.text,
        )

        ambiguous = upload(client, "歧义字段.xlsx", ambiguous_workbook())
        ambiguous_id = ambiguous.json()["dataset_id"]
        apply_p1(client, ambiguous_id)
        ambiguous_fields = client.get(f"/api/l1/datasets/{ambiguous_id}/equipment-fields").json()
        ambiguous_auto = client.post(f"/api/l1/datasets/{ambiguous_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
        ambiguous_body = ambiguous_auto.json()
        chosen = ambiguous_fields["regions"][0]
        column = next(item["column_id"] for item in chosen["candidates"] if item["header"] == "设备编号")
        chosen_apply = client.post(
            f"/api/l1/datasets/{ambiguous_id}/grouping/apply",
            json={"cross_sheet": "separate", "regions": [{"region_id": chosen["region_id"], "column_id": column, "confirmed": True}]},
        ).json()
        chosen_rows = client.get(f"/api/l1/equipment-groups/{chosen_apply['groups'][0]['group_id']}/rows").json()
        check(
            "多字段歧义进入待审核并可人工确认",
            ambiguous_fields["regions"][0]["confirmation_status"] == "needs_confirmation"
            and ambiguous_fields["regions"][0]["suggested_column_id"] is None
            and ambiguous_fields["regions"][0]["requires_review"] is True
            and ambiguous_auto.status_code == 201
            and ambiguous_body["status"] == "needs_review"
            and balanced(ambiguous_body)
            and balanced(chosen_apply)
            and chosen_apply["status"] in {"completed", "completed_with_warnings"}
            and chosen_apply["groups"][0]["equipment_id"] == "MM-01"
            and field_of(chosen_rows["rows"][0], "设备ID")["raw_value"] == "ASSET-9",
            ambiguous_auto.text,
        )

        ledger = client.post(
            f"/api/l1/datasets/{sheet_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "ledger", "ledger": [{"match_key": "MM-01", "asset_id": "ASSET-MM-01"}]},
        ).json()
        linked = next(item for item in ledger["groups"] if item["asset_id"] == "ASSET-MM-01")
        unlinked = [item for item in ledger["groups"] if item["equipment_id"] == "MM-02"]
        check(
            "台账映射才写入资产编号",
            balanced(ledger) and linked["record_count"] == 2 and linked["equipment_id"] == "MM-01" and len(unlinked) == 1 and unlinked[0]["asset_id"] is None,
        )

        first_groups = client.get(f"/api/l1/datasets/{multi_id}/equipment-groups").json()
        second_group = next(item for item in first_groups["groups"] if item["equipment_id"] == "MM-02")
        second_rows = client.get(f"/api/l1/equipment-groups/{second_group['group_id']}/rows").json()
        source_row = second_rows["rows"][0]["source_row_id"]
        remapped = client.post(
            f"/api/l1/datasets/{multi_id}/grouping/apply",
            json={
                "use_suggestions": True,
                "cross_sheet": "separate",
                "overrides": [{"source_row_id": source_row, "action": "assign", "equipment_value": "MM-01"}],
            },
        ).json()
        previous = client.get(f"/api/l1/datasets/{multi_id}/equipment-groups", params={"run_id": first_groups["run"]["run_id"]}).json()
        current_rows = client.get(f"/api/l1/equipment-groups/{remapped['groups'][0]['group_id']}/rows").json()
        moved = next(row for row in current_rows["rows"] if row["source_row_id"] == source_row)
        check(
            "修正映射会生成新修订",
            remapped["run"]["revision"] > previous["run"]["revision"]
            and balanced(remapped)
            and remapped["balance"]["equipment_count"] == 1
            and previous["balance"]["equipment_count"] == 2
            and moved["original_equipment_value"] == "MM-02"
            and moved["grouping_value"] == "MM-01"
            and moved["override_applied"] is True,
        )

        print("开始十万行", flush=True)
        lines = ["设备编号,备注\n"]
        lines.extend(f"EQ-0001,{index}\n" for index in range(100000))
        lines[-1] = "EQ-0001,尾部99999\n"
        wide = upload(client, "十万行.csv", "".join(lines).encode("utf-8"))
        wide_id = wide.json()["dataset_id"]
        wide_p1 = apply_p1(client, wide_id)
        wide_apply = client.post(f"/api/l1/datasets/{wide_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
        wide_body = wide_apply.json()
        page = client.get(
            f"/api/l1/equipment-groups/{wide_body['groups'][0]['group_id']}/rows",
            params={"offset": 99800, "limit": 200},
        ).json()
        rejected = client.get(f"/api/l1/equipment-groups/{wide_body['groups'][0]['group_id']}/rows", params={"limit": 10000})
        tail = next((row for row in page["rows"] if field_of(row, "备注") and field_of(row, "备注")["raw_value"] == "尾部99999"), None)
        check(
            "十万行分页且数量守恒",
            wide.status_code == 201
            and wide_p1.status_code == 201
            and wide_apply.status_code == 201
            and balanced(wide_body)
            and wide_body["balance"]["business_count"] == 100000
            and wide_body["balance"]["grouped_count"] == 100000
            and wide_body["balance"]["ungrouped_count"] == 0
            and page["total_records"] == 100000
            and page["offset"] == 99800
            and tail is not None
            and tail["equipment_id"] == "EQ-0001"
            and tail["source_row_id"]
            and rejected.status_code == 422,
        )
        check(
            "分组不改原件",
            (TMP / "data" / "files" / f"{sheet_id}.xlsx").read_bytes() == stored and sha256(stored) == digest,
        )

    with TestClient(app) as restarted:
        again = restarted.get(f"/api/l1/datasets/{multi_id}/equipment-groups").json()
        check(
            "重启后仍能读取 P2",
            again["applied"] is True and again["run"]["revision"] >= 2 and again["balance"]["ok"] is True,
        )
    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)
    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

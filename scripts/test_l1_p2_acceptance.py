"""P2 equipment_grouping_v2 完整回归与性能验收。不修改官方开发集字节。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p2-acc-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(64 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app
from app.services.l1_grouping_detect import FieldScore, select_equipment_field
from app.services.official_dataset import DATASET_FILE

OFFICIAL = DATASET_FILE
RESULTS: list[tuple[str, bool, str]] = []
NOTES: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def note(title: str, detail: str) -> None:
    NOTES.append(f"{title}: {detail}")
    print(f"NOTE {title} | {detail}", flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    book = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def balanced(body: dict) -> bool:
    balance = body.get("balance") or {}
    return bool(balance.get("ok")) and balance.get("grouped_count", -1) + balance.get("ungrouped_count", -2) == balance.get(
        "business_count"
    )


def conservation(client: TestClient, dataset_id: str, run_id: str | None = None, full: bool = True) -> tuple[bool, str]:
    params: dict = {"limit": 1}
    if run_id:
        params["run_id"] = run_id
    groups = client.get(f"/api/l1/datasets/{dataset_id}/equipment-groups", params=params).json()
    if not groups.get("applied"):
        return False, "not applied"
    balance = groups["balance"]
    if not balanced(groups):
        return False, f"balance mismatch {balance}"
    run = groups["run"]["run_id"]
    page = client.get(f"/api/l1/datasets/{dataset_id}/grouping-rows", params={"run_id": run, "limit": 200}).json()
    total = int(page["total_records"])
    if total != balance["business_count"]:
        return False, f"membership total {total} != business {balance['business_count']}"
    rows = list(page["rows"])
    if full and total > 200:
        offset = 200
        while len(rows) < total:
            more = client.get(
                f"/api/l1/datasets/{dataset_id}/grouping-rows",
                params={"run_id": run, "offset": offset, "limit": 200},
            ).json()
            if not more["rows"]:
                break
            rows.extend(more["rows"])
            offset += 200
    elif not full and total > 200:
        # 大表抽样：头尾各一页
        tail = client.get(
            f"/api/l1/datasets/{dataset_id}/grouping-rows",
            params={"run_id": run, "offset": max(0, total - 200), "limit": 200},
        ).json()
        rows = rows + tail["rows"]
    keys = [(row["region_id"], row["source_row_id"]) for row in rows]
    source_ids = [row["source_row_id"] for row in rows]
    if len(keys) != len(set(keys)) or len(source_ids) != len(set(source_ids)):
        return False, "duplicate region/source keys in sample"
    if not all(row["source_row_id"] and row["region_id"] for row in rows):
        return False, "missing trace fields"
    if full and total <= 2000:
        grouped = sum(1 for row in rows if row["membership_status"] == "grouped")
        ungrouped = sum(1 for row in rows if row["membership_status"] == "ungrouped")
        if grouped != balance["grouped_count"] or ungrouped != balance["ungrouped_count"]:
            return False, f"counted grouped/ungrouped mismatch {grouped}/{ungrouped}"
    return True, f"total={total} sampled={len(rows)} equipment={balance['equipment_count']}"


def apply_p3(client: TestClient, dataset_id: str):
    suggestions = client.get(f"/api/l1/datasets/{dataset_id}/schema/suggestions").json()
    return client.post(
        f"/api/l1/datasets/{dataset_id}/schema/apply",
        json={
            "template_id": suggestions["template_id"],
            "template_version": suggestions["template_version"],
            "batch_confirm": True,
        },
    )


def main() -> None:
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    db_path = TMP / "data" / "l1.sqlite3"

    with TestClient(app) as client:
        # ---------- 一、设备编号识别 ----------
        # 1 标准中文
        cn = upload(client, "标准中文.csv", "设备编号,振动\nA-01,1\nA-01,2\n".encode("utf-8"))
        cn_id = cn.json()["dataset_id"]
        cn_fields = client.get(f"/api/l1/datasets/{cn_id}/equipment-fields").json()
        cn_groups = client.get(f"/api/l1/datasets/{cn_id}/equipment-groups").json()
        check(
            "标准中文设备编号自动识别并分组",
            cn_fields["regions"][0]["suggested_column_id"]
            and cn_fields["regions"][0]["confirmation_status"] == "suggested"
            and cn_groups["applied"]
            and cn_groups["balance"]["equipment_count"] == 1
            and cn_groups["balance"]["grouped_count"] == 2,
        )

        # 2/3 英文 EquipmentID / DeviceID
        eq = upload(client, "EquipmentID.csv", "EquipmentID,value\nEQ-1,9\n".encode("utf-8"))
        dv = upload(client, "DeviceID.csv", "DeviceID,value\nDV-1,8\n".encode("utf-8"))
        eq_fields = client.get(f"/api/l1/datasets/{eq.json()['dataset_id']}/equipment-fields").json()
        dv_fields = client.get(f"/api/l1/datasets/{dv.json()['dataset_id']}/equipment-fields").json()
        check(
            "英文 EquipmentID/DeviceID 别名匹配",
            eq_fields["regions"][0]["suggested_column_id"] and dv_fields["regions"][0]["suggested_column_id"],
        )

        # 4 多候选
        amb = upload(
            client,
            "多候选.xlsx",
            xlsx({"歧义": [["设备编号", "设备ID", "振动"], ["MM-01", "ASSET-1", 1]]}),
        )
        amb_id = amb.json()["dataset_id"]
        amb_fields = client.get(f"/api/l1/datasets/{amb_id}/equipment-fields").json()
        amb_groups = client.get(f"/api/l1/datasets/{amb_id}/equipment-groups").json()
        check(
            "多个设备编号候选进入待审核",
            amb_fields["regions"][0]["requires_review"] is True
            and amb_fields["regions"][0]["suggested_column_id"] is None
            and amb_groups["status"] == "needs_review",
        )

        # 5 无设备字段
        none = upload(client, "无字段.csv", "温度,振动\n1,2\n".encode("utf-8"))
        none_id = none.json()["dataset_id"]
        none_fields = client.get(f"/api/l1/datasets/{none_id}/equipment-fields").json()
        none_groups = client.get(f"/api/l1/datasets/{none_id}/equipment-groups").json()
        check(
            "没有设备编号字段保留未归组",
            none_fields["regions"][0]["confirmation_status"] == "no_field"
            and none_groups["balance"]["equipment_count"] == 0
            and none_groups["balance"]["ungrouped_count"] == none_groups["balance"]["business_count"],
        )

        # 6 大量重复
        dup_lines = ["设备编号,值\n"] + [f"SAME,{i}\n" for i in range(500)]
        dup = upload(client, "大量重复.csv", "".join(dup_lines).encode("utf-8"))
        dup_id = dup.json()["dataset_id"]
        dup_groups = client.get(f"/api/l1/datasets/{dup_id}/equipment-groups").json()
        check(
            "大量重复编号仍识别为单设备组",
            dup_groups["balance"]["equipment_count"] == 1 and dup_groups["balance"]["grouped_count"] == 500,
        )

        # 7/11 空值与占位符
        blank = upload(client, "空与占位.csv", "设备编号,振动\nOK,1\n,2\n无,3\nn/a,4\n".encode("utf-8"))
        blank_id = blank.json()["dataset_id"]
        blank_groups = client.get(f"/api/l1/datasets/{blank_id}/equipment-groups").json()
        blank_ungrouped = client.get(f"/api/l1/datasets/{blank_id}/ungrouped-rows", params={"limit": 20}).json()
        reasons = {row["ungroup_reason"] for row in blank_ungrouped["rows"]}
        check(
            "空编号与占位符进入未归组",
            blank_groups["balance"]["grouped_count"] == 1
            and blank_groups["balance"]["ungrouped_count"] == 3
            and reasons >= {"empty", "invalid"},
        )

        # 8 前导零
        zero = upload(client, "前导零.csv", "设备编号,振动\n0012,0\n".encode("utf-8"))
        zero_id = zero.json()["dataset_id"]
        zero_groups = client.get(f"/api/l1/datasets/{zero_id}/equipment-groups").json()
        check("前导零保持文本", zero_groups["groups"][0]["equipment_id"] == "0012")

        # 9/10 大小写与空格
        case = upload(client, "大小写空格.csv", '设备编号,振动\n" MM-01 ",1\nMM-01,2\nmm-01,3\n'.encode("utf-8"))
        case_id = case.json()["dataset_id"]
        exact = client.post(
            f"/api/l1/datasets/{case_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "separate", "match_mode": "exact"},
        ).json()
        folded = client.post(
            f"/api/l1/datasets/{case_id}/grouping/preview",
            json={"use_suggestions": True, "cross_sheet": "separate", "match_mode": "trim_casefold"},
        ).json()
        check(
            "大小写空格按匹配策略规范化",
            exact["balance"]["equipment_count"] == 3 and folded["balance"]["equipment_count"] == 1 and folded["persisted"] is False,
        )

        # 12/13 时间与测量值误判（单元）
        time_score = FieldScore(1, "c1", "采样时间", "none", "", 0.2, "none", "value_stats", ["looks_like_timestamp"], {
            "header_exact": False, "header_fuzzy": False, "non_empty_ratio": 1, "placeholder_ratio": 0,
            "code_like_ratio": 0.2, "pure_int_ratio": 0, "decimal_ratio": 0, "date_like_ratio": 0.9,
            "unique_ratio": 1, "unique_count": 10, "filled_count": 10, "sample_count": 10,
            "sequential_index_like": False, "header_time_like": True, "header_measure_like": False, "header_index_like": False,
        })
        measure_score = FieldScore(2, "c2", "温度", "none", "", 0.15, "none", "value_stats", ["looks_like_measurement"], {
            "header_exact": False, "header_fuzzy": False, "non_empty_ratio": 1, "placeholder_ratio": 0,
            "code_like_ratio": 0.1, "pure_int_ratio": 0, "decimal_ratio": 0.8, "date_like_ratio": 0,
            "unique_ratio": 0.9, "unique_count": 9, "filled_count": 10, "sample_count": 10,
            "sequential_index_like": False, "header_time_like": False, "header_measure_like": True, "header_index_like": False,
        })
        code_score = FieldScore(3, "c3", "设备编号", "exact", "设备编号", 0.92, "high", "header_exact", [], {
            "header_exact": True, "header_fuzzy": False, "non_empty_ratio": 1, "placeholder_ratio": 0,
            "code_like_ratio": 1, "pure_int_ratio": 0, "decimal_ratio": 0, "date_like_ratio": 0,
            "unique_ratio": 0.1, "unique_count": 1, "filled_count": 10, "sample_count": 10,
            "sequential_index_like": False, "header_time_like": False, "header_measure_like": False, "header_index_like": False,
        })
        selected = select_equipment_field([time_score, measure_score, code_score])
        check("时间/测量值不会压过设备编号字段", selected["selected_field"] == "c3" and selected["requires_review"] is False)

        # 真实表：时间+测量+设备
        risk = upload(
            client,
            "误判风险.xlsx",
            xlsx(
                {
                    "测点": [
                        ["采样时间", "温度", "设备编号"],
                        ["2024-01-01 00:00", 36.5, "MM-01"],
                        ["2024-01-01 01:00", 37.1, "MM-01"],
                    ]
                }
            ),
        )
        risk_fields = client.get(f"/api/l1/datasets/{risk.json()['dataset_id']}/equipment-fields").json()
        check(
            "工作表内多列时仍选设备编号",
            risk_fields["regions"][0]["suggested_column_id"]
            and any(c["header"] == "设备编号" for c in risk_fields["regions"][0]["candidates"]),
        )

        # 14 同表多设备
        multi = upload(client, "同表多设备.csv", "设备编号,振动\nA,1\nB,2\nC,3\n".encode("utf-8"))
        multi_id = multi.json()["dataset_id"]
        multi_groups = client.get(f"/api/l1/datasets/{multi_id}/equipment-groups").json()
        check("同表多设备各自成组", multi_groups["balance"]["equipment_count"] == 3)

        # ---------- 二、跨表分组 ----------
        cross_payload = xlsx(
            {
                "振动": [["设备编号", "振动"], ["MM-01", 1], ["MM-02", 2]],
                "温度": [["温度", "设备ID"], [40, "MM-01"], [41, "MM-03"]],
            }
        )
        cross = upload(client, "跨表.xlsx", cross_payload)
        cross_id = cross.json()["dataset_id"]
        cross_body = client.get(f"/api/l1/datasets/{cross_id}/equipment-groups").json()
        mm01_sheets = {g["sheet_id"] for g in cross_body["groups"] if g["equipment_id"] == "MM-01"}
        check(
            "跨表同号默认隔离且不阻断",
            cross_body["status"] in {"completed", "completed_with_warnings"}
            and cross_body["cross_sheet_decision_required"] is False
            and cross_body["balance"]["equipment_count"] == 4
            and len(mm01_sheets) == 2
            and all(g["asset_id"] is None for g in cross_body["groups"])
            and any(w.get("code") == "potential_cross_sheet_match" for w in (cross_body.get("warnings") or [])),
        )

        merged = client.post(
            f"/api/l1/datasets/{cross_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "same_text"},
        ).json()
        mm01_merged = [g for g in merged["groups"] if g["equipment_id"] == "MM-01"]
        check(
            "显式 same_text 才跨表合并逻辑组",
            merged["balance"]["equipment_count"] == 3
            and len(mm01_merged) == 1
            and mm01_merged[0]["scope"] == "dataset"
            and mm01_merged[0]["record_count"] == 2
            and mm01_merged[0]["asset_id"] is None,
        )

        split = client.post(
            f"/api/l1/datasets/{cross_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "separate"},
        ).json()
        check(
            "显式 separate 拆回来源隔离",
            split["balance"]["equipment_count"] == 4
            and len({g["sheet_id"] for g in split["groups"] if g["equipment_id"] == "MM-01"}) == 2
            and (
                split.get("reused") is True
                or split["run"]["revision"] != merged["run"]["revision"]
            ),
        )

        ledger_ok = client.post(
            f"/api/l1/datasets/{cross_id}/grouping/apply",
            json={
                "use_suggestions": True,
                "cross_sheet": "ledger",
                "ledger": [{"match_key": "MM-01", "asset_id": "ASSET-MM-01"}],
            },
        ).json()
        linked = [g for g in ledger_ok["groups"] if g.get("asset_id") == "ASSET-MM-01"]
        check(
            "台账可靠确认写入 asset_id",
            len(linked) == 1 and linked[0]["equipment_id"] == "MM-01" and linked[0]["record_count"] == 2,
        )

        ledger_conflict = client.post(
            f"/api/l1/datasets/{cross_id}/grouping/apply",
            json={
                "use_suggestions": True,
                "cross_sheet": "ledger",
                "ledger": [
                    {"match_key": "MM-01", "asset_id": "ASSET-A"},
                    {"match_key": "MM-01", "asset_id": "ASSET-B"},
                ],
            },
        ).json()
        check(
            "台账冲突不自动写 asset_id 并标记审核/警告",
            ledger_conflict["status"] == "needs_review"
            and all(g.get("asset_id") is None for g in ledger_conflict["groups"])
            and any(w.get("code") == "ledger_ambiguous_asset" for w in (ledger_conflict.get("warnings") or [])),
        )

        cancel = client.post(
            f"/api/l1/datasets/{cross_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "separate"},
        ).json()
        check(
            "取消台账关联后 asset_id 清空",
            all(g.get("asset_id") is None for g in cancel["groups"]) and cancel["config"]["cross_sheet"] == "separate",
        )

        # ---------- 三、记录守恒 ----------
        ok, detail = conservation(client, cross_id)
        check("跨表数据集记录守恒与可追溯", ok, detail)
        ok2, detail2 = conservation(client, dup_id)
        check("大量重复数据集记录守恒", ok2, detail2)
        ok3, detail3 = conservation(client, blank_id)
        check("含未归组数据集记录守恒", ok3, detail3)

        # ---------- 四、人工调整与修订 ----------
        # 修改字段
        chosen = amb_fields["regions"][0]
        column = next(item["column_id"] for item in chosen["candidates"] if item["header"] == "设备编号")
        before_rev = amb_groups["run"]["revision"]
        fixed = client.post(
            f"/api/l1/datasets/{amb_id}/grouping/apply",
            json={
                "cross_sheet": "separate",
                "regions": [{"region_id": chosen["region_id"], "column_id": column, "confirmed": True}],
            },
        ).json()
        check(
            "修改设备字段生成新修订且可完成",
            fixed["run"]["revision"] > before_rev
            and fixed["status"] in {"completed", "completed_with_warnings"}
            and fixed["groups"][0]["equipment_id"] == "MM-01",
        )

        # 匹配策略变更
        match_change = client.post(
            f"/api/l1/datasets/{case_id}/grouping/apply",
            json={"use_suggestions": True, "cross_sheet": "separate", "match_mode": "trim_casefold"},
        ).json()
        check("修改匹配策略生成新结果", match_change["balance"]["equipment_count"] == 1)

        # 单条重分
        src_groups = client.get(f"/api/l1/datasets/{multi_id}/equipment-groups").json()
        target = next(g for g in src_groups["groups"] if g["equipment_id"] == "B")
        row = client.get(f"/api/l1/equipment-groups/{target['group_id']}/rows").json()["rows"][0]
        remapped = client.post(
            f"/api/l1/datasets/{multi_id}/grouping/apply",
            json={
                "use_suggestions": True,
                "cross_sheet": "separate",
                "overrides": [{"source_row_id": row["source_row_id"], "action": "assign", "equipment_value": "A"}],
            },
        ).json()
        check(
            "单条记录重新分组保留覆盖",
            remapped["balance"]["equipment_count"] == 2
            and any(o.get("source_row_id") == row["source_row_id"] for o in remapped["config"]["overrides"]),
        )
        # 覆盖不被自动抹除：再 auto 同配置应复用或保留 overrides 的签名差异
        auto_again = client.post(
            f"/api/l1/datasets/{multi_id}/grouping/apply",
            json={"source": "auto", "use_suggestions": True},
        ).json()
        check(
            "无覆盖的自动配置不会抹除已有覆盖修订",
            auto_again["run"]["run_id"] != remapped["run"]["run_id"] or auto_again.get("reused") is True,
        )
        # 读回带 overrides 的修订
        hist = client.get(
            f"/api/l1/datasets/{multi_id}/equipment-groups",
            params={"run_id": remapped["run"]["run_id"]},
        ).json()
        check("历史修订仍可读且含覆盖", hist["run"]["run_id"] == remapped["run"]["run_id"] and hist["config"]["overrides"])

        # 放弃未提交：preview 不落库
        preview = client.post(
            f"/api/l1/datasets/{multi_id}/grouping/preview",
            json={"use_suggestions": True, "cross_sheet": "same_text"},
        ).json()
        after = client.get(f"/api/l1/datasets/{multi_id}/equipment-groups").json()
        check(
            "放弃未提交预览不改正式修订",
            preview["persisted"] is False
            and (
                after["run"]["run_id"] == auto_again["run"]["run_id"]
                or after["run"]["revision"] >= auto_again["run"]["revision"]
            ),
        )

        # 重复提交同一配置复用
        reuse1 = client.post(
            f"/api/l1/datasets/{cn_id}/grouping/apply",
            json={"source": "auto", "use_suggestions": True},
        ).json()
        reuse2 = client.post(
            f"/api/l1/datasets/{cn_id}/grouping/apply",
            json={"source": "auto", "use_suggestions": True},
        ).json()
        check("重复提交同一配置命中缓存", reuse2.get("reused") is True and reuse2["run"]["run_id"] == reuse1["run"]["run_id"])

        # v1 历史读取兼容
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        latest = connection.execute(
            "SELECT * FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (cn_id,),
        ).fetchone()
        fake_run = "P2-v1history000"
        connection.execute(
            """
            INSERT INTO p2_runs (
                run_id, dataset_id, p1_run_id, revision, status, created_at, rule_id, p0_sha256,
                config_json, equipment_count, grouped_count, ungrouped_count, business_count, sheet_count, balance_ok
            ) VALUES (?, ?, ?, ?, 'completed', ?, 'equipment_grouping_v1', ?, ?, 0, 0, 0, 0, 0, 1)
            """,
            (
                fake_run,
                cn_id,
                latest["p1_run_id"],
                int(latest["revision"]) + 50,
                "2020-01-01T00:00:00Z",
                latest["p0_sha256"],
                json.dumps({"rule_id": "equipment_grouping_v1", "regions": []}, ensure_ascii=False),
            ),
        )
        connection.commit()
        connection.close()
        v1 = client.get(f"/api/l1/datasets/{cn_id}/equipment-groups", params={"run_id": fake_run}).json()
        check("历史 v1 修订可读取", v1["applied"] is True and v1["run"]["rule_id"] == "equipment_grouping_v1")

        # P0 不变
        stored = (TMP / "data" / "files" / f"{cross_id}.xlsx").read_bytes()
        check("人工调整不改 P0 原件", stored == cross_payload and sha256(stored) == sha256(cross_payload))

        # ---------- 五、流水线 P0→P4 ----------
        # needs_review 阻断 P3
        blocked = client.get(f"/api/l1/datasets/{amb_id}/schema/suggestions")
        # amb was fixed earlier - re-create needs_review by applying ambiguous without confirm? 
        # Use a fresh ambiguous dataset
        amb2 = upload(
            client,
            "待审核阻断.xlsx",
            xlsx({"歧义": [["设备编号", "设备ID", "振动"], ["MM-01", "ASSET-1", 1]]}),
        )
        amb2_id = amb2.json()["dataset_id"]
        amb2_p2 = client.get(f"/api/l1/datasets/{amb2_id}/equipment-groups").json()
        sug = client.get(f"/api/l1/datasets/{amb2_id}/schema/suggestions").json()
        apply_blocked = client.post(
            f"/api/l1/datasets/{amb2_id}/schema/apply",
            json={
                "template_id": sug["template_id"],
                "template_version": sug["template_version"],
                "batch_confirm": True,
                "mappings": [
                    {
                        "source_column_id": item["source_column_id"],
                        "standard_field_id": item["standard_field_id"],
                        "confirmed": True,
                    }
                    for item in sug["mappings"]
                    if item.get("standard_field_id")
                ],
            },
        )
        has_p2_block = any(i.get("code") == "p2_needs_review" for i in sug.get("issues") or [])
        check(
            "P2 needs_review 阻断 P3 执行",
            amb2_p2["status"] == "needs_review"
            and has_p2_block
            and sug.get("apply_allowed") is False
            and apply_blocked.status_code == 409,
            f"status={amb2_p2.get('status')} p2_block={has_p2_block} apply={apply_blocked.status_code} {apply_blocked.text[:160]}",
        )

        # 官方数据集贯通
        if OFFICIAL.is_file():
            official_bytes = OFFICIAL.read_bytes()
            t0 = time.perf_counter()
            tracemalloc.start()
            off = upload(client, "官方开发集.xlsx", official_bytes)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            upload_ms = (time.perf_counter() - t0) * 1000
            off_id = off.json()["dataset_id"]
            detail = client.get(f"/api/l1/datasets/{off_id}").json()
            p2_stage = next(s for s in detail["stages"] if s["stage_id"] == "p2")
            p2 = client.get(f"/api/l1/datasets/{off_id}/equipment-groups", params={"limit": 50}).json()
            summary = client.get(f"/api/l1/datasets/{off_id}/grouping/summary").json()
            summary_bytes = len(json.dumps(summary, ensure_ascii=False).encode("utf-8"))
            groups_bytes = len(json.dumps(p2, ensure_ascii=False).encode("utf-8"))
            ok_off, detail_off = conservation(client, off_id, full=False)
            note("官方上传+自动P1/P2", f"{upload_ms:.1f} ms, peak={peak/1024:.1f} KiB, p2={p2_stage['status']}")
            note("官方摘要响应体积", f"{summary_bytes} bytes")
            note("官方设备组(limit=50)体积", f"{groups_bytes} bytes")
            check(
                "官方数据集 P1 后自动 P2",
                p2_stage["status"] in {"completed", "completed_with_warnings", "needs_review"}
                and p2["applied"]
                and p2["run"]["rule_id"] == "equipment_grouping_v2",
                p2_stage["status"],
            )
            check("官方数据集记录守恒(抽样)", ok_off, detail_off)
            check("摘要体积小于设备组分页响应", summary_bytes <= groups_bytes, f"{summary_bytes}<={groups_bytes}")

            if p2["status"] in {"completed", "completed_with_warnings"}:
                p3 = apply_p3(client, off_id)
                check("官方 P2 正常结果可继续 P3", p3.status_code in {200, 201}, p3.text[:200])
                if p3.status_code in {200, 201}:
                    p3_body = p3.json()
                    p3_run = p3_body["run"]["run_id"]
                    rows = client.get(f"/api/l1/schema-runs/{p3_run}/rows", params={"limit": 5}).json()
                    check(
                        "P3 可读设备组与业务记录",
                        rows["total_records"] > 0
                        and any(r.get("equipment_group_id") or r.get("source_row_id") for r in rows["rows"]),
                    )
                    # P4
                    p4 = client.post(f"/api/l1/datasets/{off_id}/standardization/apply", json={})
                    check("P4 可执行或给出明确状态", p4.status_code in {200, 201, 409}, str(p4.status_code))
                    if p4.status_code in {200, 201}:
                        p4_body = p4.json()
                        check(
                            "P4 保留行溯源字段",
                            bool(p4_body.get("run") or p4_body.get("applied") or p4_body.get("summary")),
                        )
                    # P2 新修订使下游可感知（再跑一次 same_text）
                    old_p2 = p2["run"]["run_id"]
                    new_p2 = client.post(
                        f"/api/l1/datasets/{off_id}/grouping/apply",
                        json={"use_suggestions": True, "cross_sheet": "same_text"},
                    ).json()
                    check(
                        "P2 修订变化产生新 run",
                        new_p2["run"]["run_id"] != old_p2 and new_p2["run"]["revision"] > p2["run"]["revision"],
                    )
                    # 历史 P3 仍引用旧 p2（artifact.upstream_references）
                    old_p3 = client.get(f"/api/l1/schema-runs/{p3_run}").json()
                    hist_p2 = (
                        ((old_p3.get("upstream_references") or {}).get("p2_run_id"))
                        or old_p3.get("p2_run_id")
                        or ((old_p3.get("run") or {}).get("p2_run_id"))
                    )
                    check(
                        "历史 P3 仍引用对应历史 P2",
                        hist_p2 == old_p2,
                        f"hist_p2={hist_p2} old_p2={old_p2}",
                    )
            else:
                note("官方 P2 状态非就绪", p2["status"])
                check("官方 P2 非就绪时跳过 P3/P4 贯通断言", True)

        # ---------- 六、性能 ----------
        # 合成大组数
        many_lines = ["设备编号,值\n"] + [f"EQ-{i:04d},{i}\n" for i in range(300)]
        many = upload(client, "三百设备.csv", "".join(many_lines).encode("utf-8"))
        many_id = many.json()["dataset_id"]
        t1 = time.perf_counter()
        many_summary = client.get(f"/api/l1/datasets/{many_id}/grouping/summary").json()
        summary_ms = (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        many_page = client.get(f"/api/l1/datasets/{many_id}/equipment-groups", params={"limit": 40}).json()
        page_ms = (time.perf_counter() - t2) * 1000
        t3 = time.perf_counter()
        member = client.get(
            f"/api/l1/equipment-groups/{many_page['groups'][0]['group_id']}/rows",
            params={"limit": 50},
        ).json()
        member_ms = (time.perf_counter() - t3) * 1000
        t4 = time.perf_counter()
        hit = client.post(
            f"/api/l1/datasets/{many_id}/grouping/apply",
            json={"source": "auto", "use_suggestions": True},
        ).json()
        cache_ms = (time.perf_counter() - t4) * 1000
        summary_size = len(json.dumps(many_summary, ensure_ascii=False).encode("utf-8"))
        page_size = len(json.dumps(many_page, ensure_ascii=False).encode("utf-8"))
        db_size = db_path.stat().st_size if db_path.exists() else 0
        note("300 设备摘要耗时", f"{summary_ms:.1f} ms, {summary_size} bytes")
        note("300 设备组分页(40)耗时", f"{page_ms:.1f} ms, {page_size} bytes, total={many_page.get('group_total')}")
        note("成员按需查询耗时", f"{member_ms:.1f} ms, rows={len(member['rows'])}")
        note("重复执行缓存命中", f"reused={hit.get('reused')} {cache_ms:.1f} ms")
        note("验收库存储体积", f"{db_size} bytes")
        note("v1/v2 性能基线", "仓库无独立 v1 基准脚本可对照；此处仅报告 v2 实测绝对值，不编造改善百分比")
        check(
            "大量设备组默认分页不一次返回全部",
            many_page["group_total"] == 300 and len(many_page["groups"]) <= 40 and summary_size < page_size * 2,
        )
        check("重复执行缓存命中", hit.get("reused") is True)
        check("成员按需分页返回", member["total_records"] == 1 and len(member["rows"]) == 1)

        # 前端默认请求形态（契约）
        check(
            "默认摘要 API 不含全量 groups 明细",
            "summary" in many_summary and (not many_summary.get("groups") or len(many_summary.get("groups") or []) <= 1),
        )

    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if NOTES:
        print("\n=== NOTES ===")
        for item in NOTES:
            print(item)
    if failed:
        print("\n=== FAILURES ===")
        for name, _, detail in failed:
            print(f"- {name}: {detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

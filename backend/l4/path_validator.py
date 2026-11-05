"""CausalPathValidator v1.2：结构化证据角色分类，禁止宽泛子串决定 cause_specific。"""

from __future__ import annotations

from typing import Any

from l4.evidence_metadata import adapt_evidence_list
from l4.schema import ROOT_CAUSE_CONFIRMATION


def _collect_raw_evidence(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("support_evidence", "contradict_evidence", "evidence_references"):
        for row in ctx.get(key) or []:
            if isinstance(row, dict):
                items.append(row)
    bundle = ctx.get("evidence_bundle") or {}
    for row in bundle.get("evidence_items") or []:
        if isinstance(row, dict):
            items.append(row)
    return items


def _classify_meta(
    meta: dict[str, Any],
    *,
    matching: dict[str, Any],
) -> str | None:
    """返回 cause_specific | fault_manifestation | contextual | contradicting | None。"""
    assessment = str(meta.get("assessment") or "").upper()
    if "CONTRADICT" in assessment:
        # 仅当与本路径故障相关时由上层过滤；此处若特征命中路径仍可记 contradicting
        pass

    if meta.get("metadata_status") != "resolved":
        # 不可验证元数据：最多 contextual，绝不能 cause_specific
        if meta.get("evidence_id"):
            return "contextual"
        return None

    fid = meta.get("canonical_feature_id")
    group = meta.get("feature_group")
    analyzer_id = meta.get("analyzer_id")

    cause_keys = set(matching.get("cause_specific_feature_ids") or [])
    manif_keys = set(matching.get("fault_manifestation_feature_ids") or [])
    ctx_keys = set(matching.get("contextual_feature_ids") or [])
    allowed_groups = set(matching.get("allowed_feature_groups") or [])
    allowed_analyzers = set(matching.get("allowed_analyzer_ids") or [])
    same_layer = bool(matching.get("same_layer_as_resulting_fault"))

    # 精确 analyzer_id 集合匹配（禁止 blob 子串）
    if allowed_analyzers and analyzer_id not in allowed_analyzers:
        # 分析器不允许：若仍命中 manif/ctx 特征可降级，但不得 cause_specific
        if fid in manif_keys:
            return "fault_manifestation"
        if fid in ctx_keys or fid in {"vib_x", "vib_y"}:
            return "contextual"
        return "contextual"

    # feature_group 精确匹配（来自 mech 解析枚举，不是子串）
    group_ok = (not allowed_groups) or (group in allowed_groups)

    if assessment.startswith("CONTRADICT"):
        if fid in cause_keys or fid in manif_keys:
            return "contradicting"

    # 同层机理（cause ≈ resulting_fault）：特征只作故障表现，不作上游特异支持
    if same_layer:
        if fid in manif_keys or fid in cause_keys:
            return "fault_manifestation" if group_ok or fid in manif_keys else "contextual"
        if fid in ctx_keys or fid in {"vib_x", "vib_y"}:
            return "contextual"
        return None

    # cause_specific：必须 feature_id 命中 + group 允许 + 有观测
    if fid in cause_keys and group_ok and meta.get("has_observation"):
        return "cause_specific"

    if fid in manif_keys and (group_ok or not allowed_groups):
        return "fault_manifestation"

    if fid in ctx_keys or fid in {"vib_x", "vib_y"}:
        return "contextual"

    # threshold / 一般越限
    if meta.get("evidence_id_scheme") == "thr" or group == "threshold":
        return "contextual"

    return None


def validate_candidates(
    ctx: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    paths: list[dict[str, Any]],
) -> dict[str, Any]:
    metas = adapt_evidence_list(_collect_raw_evidence(ctx))
    path_by_id = {p["causal_path_id"]: p for p in paths}
    updated_candidates: list[dict[str, Any]] = []
    updated_paths: list[dict[str, Any]] = []
    global_missing: list[str] = []

    resolved_feature_ids = {m.get("canonical_feature_id") for m in metas if m.get("canonical_feature_id")}

    for cand in candidates:
        path = path_by_id.get(cand["causal_path_id"])
        if not path:
            updated_candidates.append(cand)
            continue

        matching = path.get("evidence_matching") or {}
        cause_ids: list[str] = []
        manif_ids: list[str] = []
        ctx_ids: list[str] = []
        contradict_ids: list[str] = []
        unresolved_ids: list[str] = []

        for meta in metas:
            eid = meta.get("evidence_id")
            if not eid:
                continue
            if meta.get("metadata_status") != "resolved":
                unresolved_ids.append(eid)
            role = _classify_meta(meta, matching=matching)
            if role == "cause_specific":
                cause_ids.append(eid)
            elif role == "fault_manifestation":
                manif_ids.append(eid)
            elif role == "contextual":
                ctx_ids.append(eid)
            elif role == "contradicting":
                contradict_ids.append(eid)

        def _uniq(seq: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for x in seq:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        cause_ids = _uniq(cause_ids)
        manif_ids = _uniq(manif_ids)
        ctx_ids = _uniq(ctx_ids)
        contradict_ids = _uniq(contradict_ids)
        unresolved_ids = _uniq(unresolved_ids)

        # 缺失观测：按 required_feature_ids（结构化）优先，否则回退 required_observations 文本仅作提示
        missing: list[str] = []
        req_ids = matching.get("required_feature_ids") or []
        for rid in req_ids:
            if rid not in resolved_feature_ids:
                missing.append(f"missing_feature:{rid}")
        if not req_ids:
            for req in path.get("required_observations") or []:
                # 仅记录文档要求，不再用子串判断“已出现”
                missing.append(req)

        same_layer = bool(matching.get("same_layer_as_resulting_fault"))
        cause_state = "hypothesized"
        mech_state = "knowledge_supported" if path.get("source_document_locations") else "hypothesized"
        fault_state = "observed" if manif_ids or cause_ids else "unknown"
        if contradict_ids and not cause_ids and not manif_ids:
            cause_state = "contradicted"
            fault_state = "contradicted"
        if same_layer:
            cause_state = "hypothesized"
            notes_extra = "cause 与 resulting_fault 属同层机理描述；特征仅计入 fault_manifestation。"
        else:
            notes_extra = "supporting_evidence_ids 仅含结构化 cause_specific。"

        link_states = {
            "cause": cause_state,
            "mechanism": mech_state,
            "fault_to_evidence": fault_state,
            "notes": [
                "知识来源仅说明存在候选机理，不等于本设备已发生该原因。",
                notes_extra,
                "禁止 Evidence ID 子串推断 analyzer/特征语义。",
            ],
        }
        if cand.get("evidence_insufficient_from_l3"):
            link_states["notes"].append("L3 结论不充分（INCONCLUSIVE），根因保守展开。")

        path_out = dict(path)
        path_out["link_states"] = link_states
        path_out["supporting_evidence_ids"] = cause_ids
        path_out["fault_manifestation_evidence_ids"] = manif_ids
        path_out["contextual_evidence_ids"] = ctx_ids
        path_out["contradicting_evidence_ids"] = contradict_ids
        path_out["unresolved_evidence_ids"] = unresolved_ids
        path_out["missing_evidence"] = missing
        path_out["unverified_links"] = [
            step
            for step, state in (("cause", cause_state), ("mechanism", mech_state), ("fault", fault_state))
            if state in {"hypothesized", "unknown", "knowledge_supported"}
        ]
        updated_paths.append(path_out)

        if same_layer and manif_ids and not cause_ids:
            support_summary = (
                f"同层机理：{len(manif_ids)} 条故障表现证据；不产生根因特异 supporting（仍 unconfirmed）"
            )
        elif cause_ids:
            support_summary = f"存在 {len(cause_ids)} 条结构化根因特异支持（仍 unconfirmed）"
        elif manif_ids:
            support_summary = f"仅有 {len(manif_ids)} 条故障表现证据，无 cause_specific"
        elif ctx_ids:
            support_summary = f"仅有 {len(ctx_ids)} 条上下文/未解析证据，不能证明该根因"
        else:
            support_summary = "无本机结构化根因特异证据；知识命中≠已发生"

        cand_out = dict(cand)
        cand_out["supporting_evidence_ids"] = cause_ids
        cand_out["fault_manifestation_evidence_ids"] = manif_ids
        cand_out["contextual_evidence_ids"] = ctx_ids
        cand_out["contradicting_evidence_ids"] = contradict_ids
        cand_out["unresolved_evidence_ids"] = unresolved_ids
        cand_out["missing_evidence"] = missing
        cand_out["assessment_status"] = ROOT_CAUSE_CONFIRMATION
        cand_out["support_summary"] = support_summary
        cand_out["path_link_states"] = link_states
        cand_out["same_layer_as_resulting_fault"] = same_layer
        updated_candidates.append(cand_out)
        for m in missing:
            if m not in global_missing:
                global_missing.append(m)

    return {
        "root_cause_candidates": updated_candidates,
        "causal_paths": updated_paths,
        "missing_evidence": global_missing,
    }

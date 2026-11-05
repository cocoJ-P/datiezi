"""L4 Reasoning Orchestrator：工具循环 + 精简最终 JSON；失败回退确定性评审。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from l4.reasoning import PROMPT_VERSION
from l4.reasoning.outcome import accumulate_usage, empty_audit, finalize_assessment
from l4.reasoning.path_inspector import inspect_causal_path
from l4.reasoning.policy import policy_config
from l4.reasoning.schema import assessment_skeleton, empty_candidate_review
from l4.reasoning.tools import TOOLS, ToolContext, budget_used, execute_tool

LOG = logging.getLogger("l4.reasoning.orchestrator")

SYSTEM_PROMPT = (
    "你是钢铁旋转设备 L4 根因竞争假设分析助手。"
    "工具阶段只查询与收集证据；最终阶段只输出精简 JSON，禁止复述 EvidenceBundle/知识原文/工具全文。"
    "只能引用工具返回的 candidate_id、evidence_id、causal_knowledge_id、causal_path_id。"
    "不得编造测量值、ID、设备映射或传播事实。"
    "不得将故障表现（BPFO/1X/啮合幅值等）写成已确认上游根因。"
    "即使存在 cause_specific 证据，也必须检查竞争解释，不得判定根因已成立。"
    "同层机理（same_layer_as_resulting_fault）应标为 mechanism_only 或 insufficient_upstream_evidence。"
    "不得恢复 L4 Core 已排除的故障为有效候选；若建议复核，只能写入 excluded_review_recommendations，action=review_recommended。"
    "不得改写设备适用性；不得宣称故障已跨设备传播；impact_degree 必须为 null。"
    "root_cause_confirmation 必须保持 unconfirmed。"
    "validity_status 仅允许："
    "consistent_with_observations|mechanism_only|insufficient_upstream_evidence|contradicted|"
    "indistinguishable|review_recommended|not_applicable_kept_excluded。"
    "使用中文。"
)

FINAL_JSON_INSTRUCTION = (
    "现在进入最终输出阶段：只输出一个 JSON 对象，不要 Markdown 围栏，不要工具调用。"
    "不要粘贴证据全文或知识原文；upstream/fault/contradicting/supporting 等证据字段必须是字符串 ID 数组，禁止 {evidence_id, note} 对象。"
    "短中文说明只能写在 review_summary / limitations / notes。"
    "字段："
    "candidate_reviews, causal_path_reviews, supporting_evidence_ids, contradicting_evidence_ids,"
    "missing_evidence, excluded_review_recommendations, impact_interpretation, limitations, overall_assessment。"
    "candidate_reviews 每项含 candidate_id, cause_name, source_causal_knowledge_ids, validity_status,"
    "causal_mechanism_summary, upstream_cause_evidence, fault_manifestation_evidence,"
    "contradicting_evidence, unresolved_links, alternative_explanations, review_summary,"
    "same_layer_as_resulting_fault。"
    "causal_path_reviews 每项尽量含 causal_path_id 与短评；impact_interpretation.impact_degree=null，"
    "propagation_status 仅 unknown 或 not_assessed。"
)


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _classify_json_failure(text: str, *, finish_reason: str | None) -> str:
    raw = (text or "").strip()
    if finish_reason == "length":
        return "output_truncated"
    if not raw:
        return "empty_final_content"
    if raw.startswith("{") or "{" in raw:
        try:
            json.loads(raw if raw.startswith("{") else re.search(r"\{[\s\S]*\}", raw).group(0))  # type: ignore[union-attr]
        except Exception:
            return "malformed_json"
        return "json_not_object"
    return "non_json_prose"


def build_deterministic_assessment(
    reasoning_input: dict[str, Any],
    *,
    reasoning_run_id: str,
    tool_history: list[dict[str, Any]] | None = None,
    llm_model: str = "deterministic_fallback",
) -> dict[str, Any]:
    core = reasoning_input.get("root_cause_assessment") or {}
    body = assessment_skeleton(
        reasoning_run_id=reasoning_run_id,
        source_l4_run_id=str(reasoning_input.get("source_l4_run_id")),
        source_analysis_run_id=str(reasoning_input.get("source_analysis_run_id")),
        record_id=str(reasoning_input.get("record_id")),
        llm_model=llm_model,
        prompt_version=PROMPT_VERSION,
        causal_knowledge_version=str(reasoning_input.get("causal_knowledge_version")),
        applicability_version=str(reasoning_input.get("applicability_version")),
        evidence_match_version=str(reasoning_input.get("evidence_match_version")),
        knowledge_snapshot_id=reasoning_input.get("knowledge_snapshot_id"),
        input_fingerprint=str(reasoning_input.get("input_fingerprint") or ""),
    )
    path_pack = inspect_causal_path(core)
    body["causal_path_reviews"] = path_pack.get("reviews") or []

    reviews = []
    all_support: list[str] = []
    all_contra: list[str] = []
    missing: list[str] = []
    for cand in core.get("root_cause_candidates") or []:
        rev = empty_candidate_review(
            candidate_id=str(cand.get("candidate_id")),
            cause_name=str(cand.get("cause_name") or ""),
        )
        path = next(
            (p for p in (core.get("causal_paths") or []) if p.get("causal_path_id") == cand.get("causal_path_id")),
            {},
        )
        matching = (path or {}).get("evidence_matching") or {}
        same_layer = bool(
            cand.get("same_layer_as_resulting_fault")
            or matching.get("same_layer_as_resulting_fault")
            or cand.get("causal_knowledge_id")
            in {"CK-bearing-contact-fatigue", "CK-gear-mesh-abnormality"}
        )
        rev["source_causal_knowledge_ids"] = [cand.get("causal_knowledge_id")] if cand.get("causal_knowledge_id") else []
        rev["same_layer_as_resulting_fault"] = same_layer
        rev["upstream_cause_evidence"] = list(cand.get("supporting_evidence_ids") or [])
        rev["fault_manifestation_evidence"] = list(cand.get("fault_manifestation_evidence_ids") or [])
        rev["contradicting_evidence"] = list(cand.get("contradicting_evidence_ids") or [])
        rev["unresolved_links"] = list(cand.get("missing_evidence") or [])[:8]
        rev["causal_mechanism_summary"] = str(
            ((cand.get("path_link_states") or {}).get("notes") or ["基于 L4 Core 路径状态的确定性评审"])[0]
        )
        alts = []
        for other in core.get("root_cause_candidates") or []:
            if other.get("candidate_id") == cand.get("candidate_id"):
                continue
            if other.get("related_fault_type") == cand.get("related_fault_type"):
                alts.append(other.get("cause_name"))
        rev["alternative_explanations"] = alts

        if same_layer:
            rev["validity_status"] = "mechanism_only"
            rev["review_summary"] = "该条目与故障表现同层，特征仅支持现象，不构成更上游根因证明。"
        elif rev["contradicting_evidence"]:
            rev["validity_status"] = "contradicted"
            rev["review_summary"] = "存在反对证据；根因仍 unconfirmed。"
        elif rev["upstream_cause_evidence"] and len(alts) >= 1:
            rev["validity_status"] = "indistinguishable"
            rev["review_summary"] = (
                "存在 cause_specific 观测，但有多个竞争上游解释，不足以单独确认该根因。"
            )
        elif rev["upstream_cause_evidence"]:
            rev["validity_status"] = "insufficient_upstream_evidence"
            rev["review_summary"] = (
                "存在可能相关的特异观测，但仍可有载荷/冷却/损伤等竞争解释；保持 unconfirmed。"
            )
        elif rev["fault_manifestation_evidence"]:
            rev["validity_status"] = "consistent_with_observations"
            rev["review_summary"] = "观测与故障表现一致，但缺少可区分的上游原因证据。"
        else:
            rev["validity_status"] = "insufficient_upstream_evidence"
            rev["review_summary"] = "缺少直接上游证据；候选保留为未确认。"

        reviews.append(rev)
        all_support.extend(rev["upstream_cause_evidence"])
        all_contra.extend(rev["contradicting_evidence"])
        missing.extend(rev["unresolved_links"])

    excluded_recs = []
    for ex in core.get("excluded_faults") or []:
        if ex.get("eligibility") in {
            "excluded_core_reasoning_conflict",
            "excluded_contradicted",
        }:
            excluded_recs.append(
                {
                    "fault_type": ex.get("fault_type"),
                    "action": "review_recommended",
                    "exclusion_reason": ex.get("exclusion_reason") or ex.get("reason"),
                    "contradicting_evidence_ids": ex.get("contradicting_evidence_ids") or [],
                    "needs_review": True,
                }
            )

    impact = core.get("impact_assessment") or {}
    body["candidate_reviews"] = reviews
    body["supporting_evidence_ids"] = list(dict.fromkeys(all_support))
    body["contradicting_evidence_ids"] = list(dict.fromkeys(all_contra))
    body["missing_evidence"] = list(dict.fromkeys(missing))[:20]
    body["excluded_review_recommendations"] = excluded_recs
    body["impact_interpretation"] = {
        "propagation_status": impact.get("propagation_status") or "unknown",
        "mapping_level": (impact.get("equipment_mapping") or {}).get("mapping_level"),
        "upstream_count": len(impact.get("upstream_dependencies") or []),
        "downstream_count": len(impact.get("downstream_connections") or []),
        "reference_topology_only": (impact.get("potential_impact") or {}).get("reference_topology_only"),
        "impact_degree": None,
        "notes": [
            "复述 L4 Core ImpactAssessment；不宣称已传播。",
            "类型级拓扑仅为潜在关联范围。",
        ],
    }
    body["limitations"] = list(core.get("limitations") or []) + [
        "确定性回退未调用 LLM 自由生成。",
        "cause_specific 不足以确认根因。",
        "缺少油液/相位/拆检等时常无法区分竞争假设。",
    ]
    body["overall_assessment"] = (
        f"共 {len(reviews)} 个有效根因候选，全部保持 unconfirmed；"
        f"同层机理与竞争解释已按 L4 Core 证据角色区分。"
    )
    body["tool_history_digest"] = [
        {"tool": h.get("tool"), "ok": h.get("result_ok"), "cached": bool(h.get("cached"))}
        for h in (tool_history or [])
    ]
    body["status"] = "succeeded"
    if not reviews:
        body["outcome"] = "insufficient_evidence"
    return body


def _as_id_list(value: Any) -> list[str]:
    """将 LLM 可能返回的 {evidence_id, note} 对象规范为纯 ID 字符串列表。"""
    out: list[str] = []
    if value is None:
        return out
    if not isinstance(value, list):
        value = [value]
    for item in value:
        if item is None or item == "":
            continue
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            eid = item.get("evidence_id") or item.get("id") or item.get("causal_knowledge_id") or item.get("candidate_id")
            if eid:
                out.append(str(eid))
        else:
            out.append(str(item))
    return list(dict.fromkeys(out))


def _normalize_llm_assessment(obj: dict[str, Any]) -> dict[str, Any]:
    """最终 JSON 规范化：只保留 ID 引用，不放宽引用合法性。"""
    out = dict(obj)
    for key in ("supporting_evidence_ids", "contradicting_evidence_ids", "missing_evidence"):
        if key in out:
            out[key] = _as_id_list(out.get(key))
    reviews = []
    for rev in out.get("candidate_reviews") or []:
        if not isinstance(rev, dict):
            continue
        r = dict(rev)
        for k in (
            "upstream_cause_evidence",
            "fault_manifestation_evidence",
            "contradicting_evidence",
            "unresolved_links",
            "source_causal_knowledge_ids",
        ):
            if k in r:
                r[k] = _as_id_list(r.get(k))
        if "alternative_explanations" in r and not isinstance(r.get("alternative_explanations"), list):
            r["alternative_explanations"] = [str(r.get("alternative_explanations"))]
        reviews.append(r)
    if "candidate_reviews" in out:
        out["candidate_reviews"] = reviews
    path_reviews = []
    for pr in out.get("causal_path_reviews") or []:
        if not isinstance(pr, dict):
            continue
        p = dict(pr)
        if "observation_evidence_ids" in p:
            p["observation_evidence_ids"] = _as_id_list(p.get("observation_evidence_ids"))
        path_reviews.append(p)
    if "causal_path_reviews" in out:
        out["causal_path_reviews"] = path_reviews
    return out


def _merge_llm_obj(base: dict[str, Any], llm_obj: dict[str, Any]) -> dict[str, Any]:
    llm_obj = _normalize_llm_assessment(llm_obj)
    out = dict(base)
    for key in (
        "candidate_reviews",
        "causal_path_reviews",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
        "missing_evidence",
        "excluded_review_recommendations",
        "impact_interpretation",
        "limitations",
        "overall_assessment",
    ):
        if key in llm_obj and llm_obj[key] is not None:
            out[key] = llm_obj[key]
    out["root_cause_confirmation"] = "unconfirmed"
    raw_impact = out.get("impact_interpretation")
    if isinstance(raw_impact, dict):
        impact = dict(raw_impact)
    else:
        impact = dict(base.get("impact_interpretation") or {})
        if isinstance(raw_impact, str) and raw_impact.strip():
            notes = list(impact.get("notes") or [])
            notes.append(f"llm_impact_text:{raw_impact[:300]}")
            impact["notes"] = notes
    impact["impact_degree"] = None
    impact["impact_severity_level"] = None
    if not impact.get("propagation_status"):
        impact["propagation_status"] = "unknown"
    out["impact_interpretation"] = impact
    for list_key in (
        "candidate_reviews",
        "causal_path_reviews",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
        "missing_evidence",
        "excluded_review_recommendations",
        "limitations",
    ):
        if not isinstance(out.get(list_key), list):
            out[list_key] = base.get(list_key) or []
    return out


def _id_digest(reasoning_input: dict[str, Any]) -> dict[str, Any]:
    core = reasoning_input.get("root_cause_assessment") or {}
    return {
        "candidate_ids": [c.get("candidate_id") for c in (core.get("root_cause_candidates") or [])],
        "path_ids": [p.get("causal_path_id") for p in (core.get("causal_paths") or [])],
        "causal_knowledge_ids": list(reasoning_input.get("allowed_causal_knowledge_ids") or [])[:16],
        "evidence_id_count": len(reasoning_input.get("allowed_evidence_ids") or []),
        "excluded_fault_types": list(reasoning_input.get("excluded_fault_types") or [])[:12],
    }


def _chat_create(client: Any, *, model: str, messages: list[dict[str, Any]], cfg: dict[str, Any], **extra: Any) -> Any:
    options: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "extra_body": {"thinking": {"type": "disabled"}},
        "max_tokens": int(extra.pop("max_tokens", cfg["max_output_tokens"])),
    }
    options.update(extra)
    return client.chat.completions.create(**options)


def _request_final_json(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
    audit: dict[str, Any],
    repair_hint: str | None = None,
) -> tuple[str, str | None]:
    prompt = FINAL_JSON_INSTRUCTION
    if repair_hint:
        prompt = (
            "上次输出不是合法 JSON 对象。请只输出修复后的完整 JSON 对象，不要解释。\n"
            f"失败类型提示：{repair_hint}\n"
            + FINAL_JSON_INSTRUCTION
        )
    messages.append({"role": "user", "content": prompt})
    kwargs: dict[str, Any] = {
        "max_tokens": int(cfg.get("max_final_output_tokens") or cfg["max_output_tokens"]),
    }
    # DeepSeek OpenAI 兼容：优先 json_object；若不支持由上层捕获回退
    try:
        response = _chat_create(
            client,
            model=model,
            messages=messages,
            cfg=cfg,
            response_format={"type": "json_object"},
            **kwargs,
        )
        audit["final_response_format"] = "json_object"
    except Exception as exc:  # noqa: BLE001
        LOG.info("json_object response_format unavailable, fallback plain: %s", exc)
        response = _chat_create(client, model=model, messages=messages, cfg=cfg, **kwargs)
        audit["final_response_format"] = "plain"
        audit["final_response_format_error"] = str(exc)[:200]
    accumulate_usage(audit, response)
    if not response.choices:
        return "", None
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    return choice.message.content or "", finish_reason


def run_llm_tool_loop(
    reasoning_input: dict[str, Any],
    *,
    reasoning_run_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    cfg = policy_config()
    ctx = ToolContext(reasoning_input)
    started = time.perf_counter()
    audit = empty_audit()

    # 预取：计入唯一执行与缓存，后续 LLM 重复调用走缓存不耗预算
    execute_tool("get_l4_candidates", {}, ctx)
    execute_tool("inspect_causal_path", {}, ctx)

    if dry_run:
        assessment = build_deterministic_assessment(
            reasoning_input,
            reasoning_run_id=reasoning_run_id,
            tool_history=ctx.history,
            llm_model="dry_run",
        )
        audit["tool_call_count"] = ctx.call_count
        audit["unique_tool_exec_count"] = ctx.unique_exec_count
        audit["cache_hits"] = ctx.cache_hits
        audit["execution_duration"] = round(time.perf_counter() - started, 3)
        assessment, mode = finalize_assessment(
            assessment, reasoning_input=reasoning_input, base_mode="dry_run", audit=audit
        )
        return {"assessment": assessment, "tool_history": ctx.history, "llm_rounds": 0, "mode": mode, "audit": audit}

    from app.services.deepseek import MODEL, DeepSeekError, _api_key, _raise_api_error
    from openai import OpenAI

    api_key = _api_key()
    if not api_key:
        assessment = build_deterministic_assessment(
            reasoning_input,
            reasoning_run_id=reasoning_run_id,
            tool_history=ctx.history,
            llm_model="missing_api_key_fallback",
        )
        assessment["limitations"] = list(assessment.get("limitations") or []) + ["deepseek_api_key_missing"]
        audit["error_type"] = "missing_api_key"
        audit["tool_call_count"] = ctx.call_count
        audit["unique_tool_exec_count"] = ctx.unique_exec_count
        audit["cache_hits"] = ctx.cache_hits
        audit["execution_duration"] = round(time.perf_counter() - started, 3)
        assessment, mode = finalize_assessment(
            assessment, reasoning_input=reasoning_input, base_mode="llm_failed_fallback", audit=audit
        )
        return {
            "assessment": assessment,
            "tool_history": ctx.history,
            "llm_rounds": 0,
            "mode": mode,
            "error": "DEEPSEEK_API_KEY missing",
            "audit": audit,
        }

    public_input = {
        "record_id": reasoning_input.get("record_id"),
        "equipment_type": reasoning_input.get("equipment_type"),
        "equipment_id": reasoning_input.get("equipment_id"),
        "source_l4_run_id": reasoning_input.get("source_l4_run_id"),
        "causal_knowledge_version": reasoning_input.get("causal_knowledge_version"),
        "applicability_version": reasoning_input.get("applicability_version"),
        "evidence_match_version": reasoning_input.get("evidence_match_version"),
        "input_fingerprint": reasoning_input.get("input_fingerprint"),
        "id_digest": _id_digest(reasoning_input),
        "constraints": reasoning_input.get("constraints"),
    }

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=float(cfg["timeout_seconds"]), max_retries=0)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请对以下 L4 Core 结果做根因竞争假设复核。"
                "先按需调用工具收集证据（优先 inspect_causal_path / get_l3_evidence）；"
                "不要在工具阶段输出最终 Assessment。\n"
                + json.dumps(public_input, ensure_ascii=False)
            ),
        },
    ]
    rounds = 0
    final_text = ""
    finish_reason = None
    need_forced_final = False
    try:
        max_rounds = max(1, int(cfg["max_llm_rounds"]))
        max_tools = max(1, int(cfg["max_tool_calls_per_record"]))
        while rounds < max_rounds:
            rounds += 1
            use_tools = budget_used(ctx) < max_tools
            options: dict[str, Any] = {}
            if use_tools:
                options["tools"] = TOOLS
                options["tool_choice"] = "auto"
            else:
                # 预算用尽：转入最终 JSON，不再提供 tools
                need_forced_final = True
                break
            response = _chat_create(client, model=MODEL, messages=messages, cfg=cfg, **options)
            accumulate_usage(audit, response)
            if not response.choices:
                raise DeepSeekError("DeepSeek 没有返回有效回答。", status_code=502)
            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            message = choice.message
            content = message.content or ""
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                encoded = []
                for call in tool_calls:
                    encoded.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                    )
                messages.append({"role": "assistant", "content": content or None, "tool_calls": encoded})
                for call in tool_calls:
                    # 缓存命中不耗预算；首次执行受 max_tools 限制
                    if budget_used(ctx) >= max_tools:
                        # 仍允许缓存命中
                        args_preview = call.function.arguments
                        # 试探：若会命中缓存则执行，否则返回上限错误
                        from l4.reasoning.tools import cache_key

                        try:
                            parsed_args = json.loads(args_preview or "{}")
                        except json.JSONDecodeError:
                            parsed_args = {}
                        key = cache_key(call.function.name, parsed_args if isinstance(parsed_args, dict) else {})
                        if key in ctx._cache:
                            result = execute_tool(call.function.name, call.function.arguments, ctx)
                        else:
                            result = {
                                "ok": False,
                                "tool": getattr(call.function, "name", ""),
                                "error": "已达单记录工具调用上限；请基于已有结果输出最终 JSON",
                            }
                            ctx.history.append(
                                {
                                    "tool": call.function.name,
                                    "arguments": parsed_args,
                                    "result_ok": False,
                                    "cached": False,
                                    "result": result,
                                }
                            )
                    else:
                        result = execute_tool(call.function.name, call.function.arguments, ctx)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, ensure_ascii=False)[:4500],
                        }
                    )
                continue
            # 无 tool_calls：若已有实质内容则作为候选最终稿；否则强制最终阶段
            if content.strip():
                final_text = content
                break
            need_forced_final = True
            break
        else:
            need_forced_final = True

        if need_forced_final or not _extract_json(final_text):
            # 专用最终 JSON 阶段（可含一次受控修复）
            text, fr = _request_final_json(
                client, model=MODEL, messages=messages, cfg=cfg, audit=audit
            )
            finish_reason = fr or finish_reason
            final_text = text
            if not _extract_json(final_text) and int(cfg.get("json_repair_limit") or 0) >= 1:
                fail_kind = _classify_json_failure(final_text, finish_reason=finish_reason)
                audit["json_failure_class"] = fail_kind
                text2, fr2 = _request_final_json(
                    client,
                    model=MODEL,
                    messages=messages,
                    cfg=cfg,
                    audit=audit,
                    repair_hint=fail_kind,
                )
                finish_reason = fr2 or finish_reason
                if _extract_json(text2):
                    final_text = text2
                    audit["json_repaired"] = True
                else:
                    final_text = text2 or final_text
                    audit["json_repaired"] = False
    except DeepSeekError as exc:
        LOG.warning("l4 reasoning llm failed: %s", exc.message)
        assessment = build_deterministic_assessment(
            reasoning_input,
            reasoning_run_id=reasoning_run_id,
            tool_history=ctx.history,
            llm_model=MODEL,
        )
        assessment["limitations"] = list(assessment.get("limitations") or []) + [f"llm_error:{exc.message}"]
        audit["error_type"] = "deepseek_error"
        audit["finish_reason"] = finish_reason
        audit["tool_call_count"] = ctx.call_count
        audit["unique_tool_exec_count"] = ctx.unique_exec_count
        audit["cache_hits"] = ctx.cache_hits
        audit["execution_duration"] = round(time.perf_counter() - started, 3)
        assessment, mode = finalize_assessment(
            assessment, reasoning_input=reasoning_input, base_mode="llm_failed_fallback", audit=audit
        )
        return {
            "assessment": assessment,
            "tool_history": ctx.history,
            "llm_rounds": rounds,
            "mode": mode,
            "error": exc.message,
            "audit": audit,
        }
    except Exception as exc:  # noqa: BLE001
        try:
            _raise_api_error(exc)
        except DeepSeekError as ds:
            assessment = build_deterministic_assessment(
                reasoning_input,
                reasoning_run_id=reasoning_run_id,
                tool_history=ctx.history,
                llm_model=MODEL,
            )
            assessment["limitations"] = list(assessment.get("limitations") or []) + [f"llm_error:{ds.message}"]
            audit["error_type"] = "deepseek_error"
            audit["tool_call_count"] = ctx.call_count
            audit["unique_tool_exec_count"] = ctx.unique_exec_count
            audit["cache_hits"] = ctx.cache_hits
            audit["execution_duration"] = round(time.perf_counter() - started, 3)
            assessment, mode = finalize_assessment(
                assessment, reasoning_input=reasoning_input, base_mode="llm_failed_fallback", audit=audit
            )
            return {
                "assessment": assessment,
                "tool_history": ctx.history,
                "llm_rounds": rounds,
                "mode": mode,
                "error": ds.message,
                "audit": audit,
            }
        raise
    finally:
        client.close()

    base = build_deterministic_assessment(
        reasoning_input,
        reasoning_run_id=reasoning_run_id,
        tool_history=ctx.history,
        llm_model=MODEL,
    )
    llm_obj = _extract_json(final_text)
    audit["finish_reason"] = finish_reason
    audit["tool_call_count"] = ctx.call_count
    audit["unique_tool_exec_count"] = ctx.unique_exec_count
    audit["cache_hits"] = ctx.cache_hits
    audit["execution_duration"] = round(time.perf_counter() - started, 3)
    audit["final_text_chars"] = len(final_text or "")

    if not llm_obj:
        fail_kind = _classify_json_failure(final_text, finish_reason=finish_reason)
        assessment = base
        assessment["limitations"] = list(assessment.get("limitations") or []) + [
            "llm_invalid_json",
            f"json_failure_class:{fail_kind}",
            "used_deterministic_alignment_after_invalid_llm_json",
        ]
        audit["error_type"] = "finish_reason_length" if fail_kind == "output_truncated" else "invalid_json"
        audit["json_failure_class"] = fail_kind
        assessment, mode = finalize_assessment(
            assessment, reasoning_input=reasoning_input, base_mode="llm_invalid_json_fallback", audit=audit
        )
        return {
            "assessment": assessment,
            "tool_history": ctx.history,
            "llm_rounds": rounds,
            "mode": mode,
            "raw_text": (final_text or "")[:2000],
            "audit": audit,
        }

    assessment = _merge_llm_obj(base, llm_obj)
    assessment["model_id"] = MODEL
    assessment, mode = finalize_assessment(
        assessment, reasoning_input=reasoning_input, base_mode="live_llm_success", audit=audit
    )
    # 证据不足但仍是合法 LLM 成功：升级 outcome 标签（不改校验）
    if mode == "live_llm_success":
        reviews = assessment.get("candidate_reviews") or []
        if not reviews:
            assessment["outcome"] = "insufficient_evidence"
            mode = "insufficient_evidence"
        else:
            weak = {"insufficient_upstream_evidence", "mechanism_only", "indistinguishable", "contradicted"}
            if reviews and all(str(r.get("validity_status")) in weak for r in reviews if isinstance(r, dict)):
                # 仍标 live_llm_success；证据不足写在 limitations，避免与执行失败混淆
                assessment["limitations"] = list(assessment.get("limitations") or []) + [
                    "causal_evidence_insufficient_but_llm_succeeded"
                ]
    return {
        "assessment": assessment,
        "tool_history": ctx.history,
        "llm_rounds": rounds,
        "mode": mode,
        "audit": audit,
    }

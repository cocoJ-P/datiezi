"""从 DiagnosisRun + PED 装配 AnalysisContext（禁止读取 labels）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from l2.equipment.registry import get_equipment_spec
from l2.handoff.store import HANDOFF_STORE, HandoffStore
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.routing_policy import ROUTING_POLICY_VERSION, trigger_reason_for
from l3 import ANALYZER_CONFIG_VERSION
from l3.core.context import AnalysisContext, HistoryPoint
from l3.runtime.thresholds import load_threshold_rules


def _parse_time(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(text, errors="coerce").to_pydatetime()
    except Exception:  # noqa: BLE001
        return None


class LabelLeakError(RuntimeError):
    """正式分析路径禁止读取标签。"""


class GuardedPreparedAdapter:
    """包装 PED 适配器，阻断 load_labels。"""

    def __init__(self, inner: PreparedDatasetAdapter):
        self._inner = inner
        self.prepared_dataset_id = inner.prepared_dataset_id
        self.meta = inner.meta

    def load_features(self) -> pd.DataFrame:
        return self._inner.load_features()

    def load_metadata(self) -> pd.DataFrame:
        return self._inner.load_metadata()

    def get_dataset_metadata(self) -> dict[str, Any]:
        return self._inner.get_dataset_metadata()

    def load_labels(self) -> pd.DataFrame:  # noqa: D401
        raise LabelLeakError("L3 正式分析路径禁止调用 load_labels()。")

    def __getattr__(self, name: str) -> Any:
        if name == "load_labels":
            raise LabelLeakError("L3 正式分析路径禁止调用 load_labels()。")
        return getattr(self._inner, name)


class PedIndex:
    """按 PED 修订预加载特征与元数据，避免逐条读盘。"""

    def __init__(self, prepared_dataset_id: str, equipment_type: str):
        raw = PreparedDatasetAdapter(prepared_dataset_id)
        self.adapter = GuardedPreparedAdapter(raw)
        self.equipment_type = equipment_type
        self.spec = get_equipment_spec(equipment_type)
        self.features = self.adapter.load_features()
        self.metadata = self.adapter.load_metadata()
        self.id_col = self.spec.record_id_field
        if self.id_col not in self.features.columns and "record_id" in self.features.columns:
            self.id_col = "record_id"
        self.meta_id = self.id_col if self.id_col in self.metadata.columns else (
            "record_id" if "record_id" in self.metadata.columns else self.id_col
        )
        self._feature_by_id: dict[str, dict[str, float]] = {}
        for _, row in self.features.iterrows():
            rid = str(row[self.id_col])
            vals: dict[str, float] = {}
            for name in self.spec.feature_names:
                if name not in self.features.columns:
                    continue
                raw_v = row[name]
                try:
                    if pd.isna(raw_v):
                        continue
                    vals[name] = float(raw_v)
                except (TypeError, ValueError):
                    continue
            self._feature_by_id[rid] = vals

        self._meta_by_id: dict[str, dict[str, Any]] = {}
        for _, row in self.metadata.iterrows():
            rid = str(row[self.meta_id])
            eid = row.get("equipment_id") if "equipment_id" in self.metadata.columns else None
            ctime = row.get("collection_time") if "collection_time" in self.metadata.columns else None
            if ctime is None and self.spec.timestamp_field in self.metadata.columns:
                ctime = row.get(self.spec.timestamp_field)
            self._meta_by_id[rid] = {
                "equipment_id": None if eid is None or str(eid).strip() == "" else str(eid).strip(),
                "collection_time": None if ctime is None or (isinstance(ctime, float) and pd.isna(ctime)) else str(ctime),
            }

        # 按设备建立历史索引
        self._by_equipment: dict[str | None, list[tuple[datetime | None, str]]] = {}
        for rid, meta in self._meta_by_id.items():
            key = meta.get("equipment_id")
            t = _parse_time(meta.get("collection_time"))
            self._by_equipment.setdefault(key, []).append((t, rid))
        for key, items in self._by_equipment.items():
            items.sort(key=lambda x: (x[0] is None, x[0] or datetime.min, x[1]))

        self.threshold_rules = load_threshold_rules(equipment_type)

    def features_for(self, record_id: str) -> dict[str, float]:
        return dict(self._feature_by_id.get(str(record_id)) or {})

    def meta_for(self, record_id: str) -> dict[str, Any]:
        return dict(self._meta_by_id.get(str(record_id)) or {})

    def history_before(self, record_id: str, *, limit: int = 200) -> tuple[list[HistoryPoint], str]:
        meta = self.meta_for(record_id)
        equipment_id = meta.get("equipment_id")
        current_t = _parse_time(meta.get("collection_time"))
        note = "historical_observation_reference"
        if equipment_id is None:
            return [], "no_equipment_id_single_record_only"
        series = self._by_equipment.get(equipment_id) or []
        if current_t is None:
            return [], "invalid_or_missing_collection_time"
        points: list[HistoryPoint] = []
        for t, rid in series:
            if rid == str(record_id):
                continue
            if t is None or t >= current_t:
                continue
            points.append(
                HistoryPoint(
                    record_id=rid,
                    collection_time=self.meta_for(rid).get("collection_time"),
                    features=self.features_for(rid),
                )
            )
        if len(points) < 3:
            note = "insufficient_history"
        return points[-limit:], note


class InputAdapter:
    def __init__(self, store: HandoffStore | None = None):
        self.store = store or HANDOFF_STORE

    def load_ped_index(self, *, prepared_dataset_id: str, equipment_type: str) -> PedIndex:
        return PedIndex(prepared_dataset_id, equipment_type)

    def build_context(
        self,
        *,
        analysis_run_id: str,
        diagnosis_run: dict[str, Any],
        record_row: dict[str, Any],
        ped_index: PedIndex,
        manual: bool = False,
    ) -> AnalysisContext:
        result = record_row.get("result") or {}
        decision = result.get("decision") or {}
        screening = record_row.get("screening_result") or decision.get("screening_result")
        rid = str(record_row["record_id"])
        meta = ped_index.meta_for(rid)
        history, note = ped_index.history_before(rid)
        return AnalysisContext(
            analysis_run_id=analysis_run_id,
            record_id=rid,
            equipment_type=str(diagnosis_run.get("equipment_type") or ped_index.equipment_type),
            equipment_id=meta.get("equipment_id"),
            prepared_dataset_id=str(diagnosis_run.get("prepared_dataset_id") or ""),
            dataset_revision_id=str(diagnosis_run.get("dataset_revision_id") or ""),
            diagnosis_run_id=str(diagnosis_run.get("diagnosis_run_id") or ""),
            model_bundle_id=diagnosis_run.get("model_bundle_id"),
            features=ped_index.features_for(rid),
            feature_names=list(ped_index.spec.feature_names),
            collection_time=meta.get("collection_time"),
            l2_decision=decision,
            l2_binary=result.get("binary_classifier") or {},
            l2_multiclass=result.get("multiclass_classifier") or {},
            l2_consistency=result.get("consistency") or {},
            l2_spectral_reference=result.get("spectral_reference"),
            trigger_reason=trigger_reason_for(screening, manual=manual),
            routing_policy_version=ROUTING_POLICY_VERSION,
            history_before=history,
            history_note=note,
            schema_version=diagnosis_run.get("schema_version") or ped_index.meta.get("schema_version"),
            analyzer_config_version=ANALYZER_CONFIG_VERSION,
            threshold_rules=list(ped_index.threshold_rules),
        )

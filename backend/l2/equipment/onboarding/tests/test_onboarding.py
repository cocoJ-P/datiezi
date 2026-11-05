"""Equipment Onboarding v1 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from l2.equipment.onboarding.draft import build_draft_from_pd
from l2.equipment.onboarding.register import register_equipment_spec
from l2.equipment.onboarding.service import EquipmentOnboardingService
from l2.equipment.onboarding.store import OnboardingStore
from l2.equipment.onboarding.validate import validate_draft
from l2.equipment.registry import get_equipment_spec, has_equipment, load_all_specs
from l2.prepared.mapping import EquipmentMapping
from l2.prepared.publisher import publish_from_l1_prepared

PUMP_PD = "PD-4c2a075ce5f8"


@pytest.fixture()
def runtime_eq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "l2_equipment"
    monkeypatch.setenv("L2_EQUIPMENT_RUNTIME_DIR", str(root))
    monkeypatch.setenv("L2_ONBOARDING_DB", str(tmp_path / "onboarding.sqlite3"))
    monkeypatch.setenv("L2_PREPARED_ROOT", str(tmp_path / "prepared"))
    load_all_specs(refresh=True)
    return root


def test_registered_equipment_reuses_without_forced_onboarding(runtime_eq):
    draft = build_draft_from_pd(pd_revision_id=PUMP_PD)
    assert draft["already_registered"] is True
    assert draft["mapping_status"] == "confirmed"
    assert draft["equipment_type_candidate"] == "high_pressure_descaling_pump"
    assert draft["feature_count"] >= 10
    assert "正常" in draft["class_labels"]
    assert draft["normal_label_candidate"] == "正常"


def test_draft_excludes_leakage_and_keeps_numeric_features(runtime_eq):
    draft = build_draft_from_pd(pd_revision_id=PUMP_PD)
    ids = {f["feature_id"] for f in draft["feature_candidates"]}
    assert "fault_type" not in ids
    assert "fault_severity" not in ids
    assert any(f["selected"] for f in draft["feature_candidates"])
    selected = [f for f in draft["feature_candidates"] if f["selected"]]
    assert all(f["numeric_count"] > 0 for f in selected)


def test_validate_blocks_missing_normal_and_leakage(runtime_eq):
    draft = build_draft_from_pd(pd_revision_id=PUMP_PD)
    bad = validate_draft(
        draft,
        user_overrides={
            "normal_label": None,
            "selected_feature_ids": ["fault_type"],
        },
    )
    # fault_type 不在 feature_candidates selected path；强制加入
    draft2 = dict(draft)
    draft2["feature_candidates"] = [
        {
            "feature_id": "fault_type",
            "name": "故障类型",
            "selected": True,
            "leakage": True,
            "numeric_count": 10,
        }
    ]
    draft2["normal_label_candidate"] = None
    result = validate_draft(draft2)
    assert result["ok"] is False
    codes = {e["code"] for e in result["errors"]}
    assert "LEAKAGE_FEATURE" in codes
    assert "NORMAL_LABEL_REQUIRED" in codes


def test_register_new_equipment_and_publish_ped(runtime_eq, monkeypatch):
    draft = build_draft_from_pd(pd_revision_id=PUMP_PD)
    draft["equipment_type_candidate"] = "test_onboarding_device"
    draft["display_name_candidate"] = "测试新设备"
    draft["already_registered"] = False
    draft["equipment_category"] = "测试新设备"
    # 确保特征有中文名可用
    assert draft["feature_count"] > 0
    result = validate_draft(draft)
    assert result["ok"] is True
    registered = register_equipment_spec(result["merged_draft"])
    assert registered["equipment_type"] == "test_onboarding_device"
    assert registered["reused"] is False
    load_all_specs(refresh=True)
    assert has_equipment("test_onboarding_device")
    spec = get_equipment_spec("test_onboarding_device", refresh=True)
    assert spec.feature_count == draft["feature_count"]
    assert spec.normal_label == "正常"

    # 重复注册同 Schema 应复用
    again = register_equipment_spec(result["merged_draft"])
    assert again["reused"] is True

    published = publish_from_l1_prepared(
        PUMP_PD,
        equipment_type="test_onboarding_device",
        equipment_category="高压除鳞泵",
    )
    assert published["prepared_dataset_id"].startswith("PED-")
    assert published["equipment_type"] == "test_onboarding_device"
    assert published["feature_count"] == spec.feature_count


def test_onboarding_service_prepare_and_confirm_idempotent(runtime_eq, monkeypatch):
    store = OnboardingStore()
    service = EquipmentOnboardingService(store=store)

    def fake_resolve(**kwargs):
        return EquipmentMapping(
            equipment_type="",
            schema_version="",
            mapping_status="needs_review",
            mapping_source="test",
            needs_confirmation=True,
            candidate_spec={"hint": "new"},
        )

    monkeypatch.setattr("l2.equipment.onboarding.draft.resolve_equipment_type", fake_resolve)
    monkeypatch.setattr("l2.equipment.onboarding.service.publish_from_l1_prepared", lambda *a, **k: {
        "prepared_dataset_id": "PED-testob001",
        "revision_id": "rev-test",
        "equipment_type": "eq_test_new",
        "training_ready": True,
        "inference_ready": True,
        "reused": False,
    })
    monkeypatch.setattr(
        "l2.equipment.onboarding.service.register_equipment_spec",
        lambda draft: {
            "reused": False,
            "equipment_type": draft["equipment_type_candidate"],
            "schema_version": draft["schema_version_candidate"],
            "feature_count": draft["feature_count"],
            "class_count": draft["class_count"],
        },
    )
    monkeypatch.setattr("l2.equipment.onboarding.service.load_all_specs", lambda refresh=False: {})

    from l2.prepared.l1_source import get_l1_prepared_meta

    meta = get_l1_prepared_meta(PUMP_PD)
    first = service.prepare(dataset_id=meta.dataset_id, pd_revision_id=PUMP_PD)
    assert first["needs_confirmation"] is True
    assert first["onboarding_id"].startswith("OB-")
    second = service.prepare(dataset_id=meta.dataset_id, pd_revision_id=PUMP_PD)
    assert second["onboarding_id"] == first["onboarding_id"]

    confirmed = service.confirm(
        first["onboarding_id"],
        overrides={
            "equipment_type": "eq_test_new",
            "display_name": "测试新设备",
            "normal_label": "正常",
        },
        continue_handoff=False,
    )
    assert confirmed["status"] == "registered"
    assert confirmed["register"]["equipment_type"] == "eq_test_new"
    assert confirmed["ped"]["prepared_dataset_id"] == "PED-testob001"

    again = service.confirm(first["onboarding_id"], continue_handoff=False)
    assert again["register"]["reused"] is True


def test_existing_pump_unaffected(runtime_eq):
    load_all_specs(refresh=True)
    assert has_equipment("high_pressure_descaling_pump")
    assert has_equipment("main_mill_motor")
    pump = get_equipment_spec("high_pressure_descaling_pump", refresh=True)
    mill = get_equipment_spec("main_mill_motor", refresh=True)
    assert pump.feature_count == 14
    assert mill.feature_count == 12

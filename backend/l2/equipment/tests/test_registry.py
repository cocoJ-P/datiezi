from __future__ import annotations

from l2.equipment.registry import get_equipment_spec, list_equipment_types
from l2.equipment.validation import validate_equipment_spec


def test_two_equipment_specs_differ():
    mill = get_equipment_spec("main_mill_motor")
    pump = get_equipment_spec("high_pressure_descaling_pump")
    validate_equipment_spec(mill)
    validate_equipment_spec(pump)
    assert mill.feature_count == 12
    assert pump.feature_count == 14
    assert mill.class_count == 5
    assert pump.class_count == 3
    assert mill.schema_version != pump.schema_version
    assert set(mill.feature_names) != set(pump.feature_names)
    assert "转频(Hz)" in pump.feature_names
    assert "转频(Hz)" not in mill.feature_names
    assert "润滑故障" in mill.class_labels
    assert "润滑故障" not in pump.class_labels
    assert {s.equipment_type for s in list_equipment_types()} >= {
        "main_mill_motor",
        "high_pressure_descaling_pump",
    }


def test_label_fields_not_in_features():
    for name in ("main_mill_motor", "high_pressure_descaling_pump"):
        spec = get_equipment_spec(name)
        spec.assert_no_leakage(spec.feature_names)
        assert spec.target_field not in spec.feature_names
        assert spec.normal_label in spec.class_labels

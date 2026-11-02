"""L2 设备类型注册。"""

from l2.equipment.registry import get_equipment_spec, list_equipment_types, load_all_specs
from l2.equipment.schema import EquipmentSpec, FeatureDef
from l2.equipment.validation import PreparedEquipmentDataset, validate_equipment_spec

__all__ = [
    "EquipmentSpec",
    "FeatureDef",
    "PreparedEquipmentDataset",
    "get_equipment_spec",
    "list_equipment_types",
    "load_all_specs",
    "validate_equipment_spec",
]

"""钢铁设备诊断 Schema v1。

模板描述标准字段，不绑定某一个工作簿。
这里只声明目标类型和单位，不转换原始单元格。
"""

from __future__ import annotations

from dataclasses import dataclass

TEMPLATE_ID = "steel_equipment_diagnosis_v1"
TEMPLATE_VERSION = "1.0.0"
TEMPLATE_NAME = "钢铁设备诊断字段"
TEMPLATE_SUMMARY = "设备标识、工况、振动、轴承频域、温度、标签和已有诊断输出。缺少的列可以保持未映射。"
TEMPLATE_SCOPE = "钢铁机械设备状态监测与故障诊断"
RULE_ID = "schema_mapping_v1"

ROLES = (
    "identifier",
    "timestamp",
    "numerical_feature",
    "categorical_feature",
    "context",
    "target_label",
    "diagnostic_output",
    "dataset_metadata",
    "unmapped",
)
ELIGIBILITY = ("candidate_input", "excluded", "requires_review")
MATCH_METHODS = ("exact_match", "alias_match", "normalized_match", "similarity", "ambiguous", "manual", "unmapped")
CONFIRMATIONS = ("suggested", "needs_confirmation", "confirmed")


@dataclass(frozen=True)
class StandardField:
    field_id: str
    label: str
    description: str
    physical_meaning: str
    role: str
    target_type: str
    target_unit: str
    dimension: str
    quantity: str
    required: bool
    eligibility: str
    uncertain: bool
    unit_sensitive: bool
    aliases: tuple[str, ...]


def _field(
    field_id: str,
    label: str,
    description: str,
    meaning: str,
    role: str,
    target_type: str,
    target_unit: str,
    dimension: str,
    quantity: str,
    required: bool,
    eligibility: str,
    uncertain: bool,
    unit_sensitive: bool,
    aliases: tuple[str, ...],
) -> StandardField:
    return StandardField(
        field_id,
        label,
        description,
        meaning,
        role,
        target_type,
        target_unit,
        dimension,
        quantity,
        required,
        eligibility,
        uncertain,
        unit_sensitive,
        aliases,
    )


FIELDS: tuple[StandardField, ...] = (
    _field("equipment_id", "设备编号", "设备身份标识", "被监测设备的编号", "identifier", "string", "", "identity", "", True, "excluded", False, False, ("设备编号", "设备ID", "资产编号", "EquipmentID", "DeviceID")),
    _field("equipment_category", "设备类别", "设备所属类别", "设备类别或设备类型", "context", "string", "", "category", "", False, "candidate_input", False, False, ("设备类别", "设备类型")),
    _field("record_id", "记录编号", "单条记录标识", "原始记录编号", "identifier", "string", "", "identity", "", False, "excluded", False, False, ("记录编号", "记录ID")),
    _field("timestamp", "采集时间", "观测采集时间", "这条记录的采集时间", "timestamp", "timestamp", "", "time", "", True, "candidate_input", False, False, ("采集时间", "timestamp")),
    _field("rotational_speed", "转速", "机械转速", "轴或转子的转速", "numerical_feature", "float64", "rpm", "rotational_speed", "speed", False, "candidate_input", False, False, ("转速", "转速(rpm)")),
    _field("rotational_frequency", "转频", "转动频率", "转轴转动频率", "numerical_feature", "float64", "Hz", "frequency", "frequency", False, "candidate_input", False, False, ("转频", "转频(Hz)")),
    _field("motor_current", "电机电流", "电机电流", "驱动电机电流", "numerical_feature", "float64", "A", "current", "", False, "candidate_input", False, False, ("电机电流", "电机电流(A)", "电流")),
    _field("vibration_x", "X向振动", "X向振动速度", "X方向振动速度", "numerical_feature", "float64", "mm/s", "velocity", "vibration", False, "candidate_input", False, True, ("X向振动", "X向振动(mm/s)", "X向振动速度")),
    _field("vibration_y", "Y向振动", "Y向振动速度", "Y方向振动速度", "numerical_feature", "float64", "mm/s", "velocity", "vibration", False, "candidate_input", False, True, ("Y向振动", "Y向振动(mm/s)", "Y向振动速度")),
    _field("vibration_max", "最大振动", "最大振动速度", "最大振动速度", "numerical_feature", "float64", "mm/s", "velocity", "vibration", False, "candidate_input", False, True, ("最大振动", "最大振动(mm/s)")),
    _field("vibration_rms", "振动均方根", "振动均方根", "振动速度均方根。原列没有单位时需要复核。", "numerical_feature", "float64", "mm/s", "velocity", "vibration", False, "requires_review", False, True, ("振动均方根", "振动RMS")),
    _field("envelope_peak", "包络谱峰值", "包络加速度峰值", "包络谱峰值", "numerical_feature", "float64", "g", "acceleration", "amplitude", False, "candidate_input", False, True, ("包络谱峰值", "包络谱峰值(g)")),
    _field("bpfo_amplitude", "BPFO幅值", "外圈故障特征幅值", "轴承外圈通过频率的幅值", "numerical_feature", "float64", "g", "acceleration", "amplitude", False, "candidate_input", False, True, ("BPFO幅值", "BPFO幅值(g)")),
    _field("bpfi_amplitude", "BPFI幅值", "内圈故障特征幅值", "轴承内圈通过频率的幅值", "numerical_feature", "float64", "g", "acceleration", "amplitude", False, "candidate_input", False, True, ("BPFI幅值", "BPFI幅值(g)")),
    _field("bsf_amplitude", "BSF幅值", "滚动体故障特征幅值", "滚动体自转频率的幅值", "numerical_feature", "float64", "g", "acceleration", "amplitude", False, "candidate_input", False, True, ("BSF幅值", "BSF幅值(g)")),
    _field("gear_mesh_amplitude", "啮合频率幅值", "齿轮啮合幅值", "齿轮啮合频率的幅值", "numerical_feature", "float64", "g", "acceleration", "amplitude", False, "candidate_input", False, True, ("啮合频率幅值", "啮合频率幅值(g)")),
    _field("amplitude_1x", "1X转频幅值", "一倍转频幅值", "一倍转频处的振动幅值", "numerical_feature", "float64", "mm/s", "velocity", "amplitude", False, "candidate_input", False, True, ("1X转频幅值", "1X转频幅值(mm/s)", "1X幅值")),
    _field("bpfo_frequency", "BPFO频率", "外圈故障特征频率", "轴承外圈通过频率", "numerical_feature", "float64", "Hz", "frequency", "frequency", False, "candidate_input", False, True, ("BPFO频率", "BPFO频率(Hz)")),
    _field("bpfi_frequency", "BPFI频率", "内圈故障特征频率", "轴承内圈通过频率", "numerical_feature", "float64", "Hz", "frequency", "frequency", False, "candidate_input", False, True, ("BPFI频率", "BPFI频率(Hz)")),
    _field("bsf_frequency", "BSF频率", "滚动体故障特征频率", "滚动体自转频率", "numerical_feature", "float64", "Hz", "frequency", "frequency", False, "candidate_input", False, True, ("BSF频率", "BSF频率(Hz)")),
    _field("gear_mesh_frequency", "啮合频率", "齿轮啮合频率", "齿轮啮合频率", "numerical_feature", "float64", "Hz", "frequency", "frequency", False, "candidate_input", False, True, ("啮合频率", "啮合频率(Hz)")),
    _field("bearing_temperature_drive_end", "驱动端轴承温度", "驱动端轴承温度", "驱动端轴承温度", "numerical_feature", "float64", "°C", "temperature", "", False, "candidate_input", False, True, ("驱动端轴承温度", "驱动端轴承温度(°C)")),
    _field("bearing_temperature_non_drive_end", "非驱动端轴承温度", "非驱动端轴承温度", "非驱动端轴承温度", "numerical_feature", "float64", "°C", "temperature", "", False, "candidate_input", False, True, ("非驱动端轴承温度", "非驱动端轴承温度(°C)")),
    _field("equipment_status", "设备状态", "运行状态", "可能是现场观测，也可能是事后结论，不能只凭列名当作预测输入。", "categorical_feature", "string", "", "text", "", False, "requires_review", True, False, ("设备状态",)),
    _field("fault_type", "故障类型", "监督标签", "故障类型标签，不能作为预测输入。", "target_label", "string", "", "text", "", False, "excluded", False, False, ("故障类型",)),
    _field("fault_severity", "故障严重度", "严重度", "可能是标签或事后评价，默认不进入预测输入。", "target_label", "string", "", "text", "", False, "excluded", True, False, ("故障严重度",)),
    _field("fault_location", "故障位置", "监督标签", "故障位置标签，不能作为预测输入。", "target_label", "string", "", "text", "", False, "excluded", False, False, ("故障位置",)),
    _field("diagnosis_confidence", "诊断置信度", "已有诊断输出", "既有诊断给出的置信度。", "diagnostic_output", "string", "", "text", "", False, "excluded", False, False, ("诊断置信度",)),
    _field("spectrum_description", "频谱描述", "已有诊断描述", "既有频谱分析文字。", "diagnostic_output", "string", "", "text", "", False, "excluded", False, False, ("频谱描述",)),
    _field("root_cause", "根因分析", "已有诊断输出", "既有根因分析，不能作为预测输入。", "diagnostic_output", "string", "", "text", "", False, "excluded", False, False, ("根因分析",)),
    _field("recommended_action", "建议措施", "已有诊断输出", "既有建议措施。", "diagnostic_output", "string", "", "text", "", False, "excluded", False, False, ("建议措施",)),
    _field("priority", "优先级", "事后优先级", "可能是事后诊断信息，默认不进入预测输入。", "diagnostic_output", "string", "", "text", "", False, "excluded", True, False, ("优先级",)),
    _field("impact_level", "影响程度", "事后影响", "可能是事后诊断信息，默认不进入预测输入。", "diagnostic_output", "string", "", "text", "", False, "excluded", True, False, ("影响程度",)),
    _field("dataset_split", "数据集划分", "数据集划分", "训练、验证或测试划分，不作为设备观测。", "dataset_metadata", "string", "", "text", "", False, "excluded", False, False, ("数据集划分",)),
)

FIELD_BY_ID = {item.field_id: item for item in FIELDS}
BARE_AMBIGUOUS = {
    "bpfo": ("bpfo_amplitude", "bpfo_frequency"),
    "bpfi": ("bpfi_amplitude", "bpfi_frequency"),
    "bsf": ("bsf_amplitude", "bsf_frequency"),
}

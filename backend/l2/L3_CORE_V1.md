# L3 Core v1

深度故障分析与结构化证据引擎（无 LLM / RAG / 知识图谱）。

## 分流

`l2_l3_routing_v2`：NORMAL → L2 完成；ABNORMAL / CONFLICT / UNCERTAIN → 自动 L3。

## 能力

- InputAdapter + 同设备历史（禁止 labels）
- Statistical / Threshold / Mechanical / EvidenceReviewer
- EvidenceBundle 不可变落盘
- AnalysisRun + Worker + `/l3` API
- 总览真实进度 + L3 证据工作台

完成后停止于 L3 Core；不自动实施 Knowledge / Reasoning / L4 / L5。

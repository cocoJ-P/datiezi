# L2 v1.1 功能定稿（冻结）

**状态：功能定稿。不再在 L2 扩展严重度、位置、根因、风险、维修建议等业务能力。**

## L2 最终能力

1. 多设备数据自动接入（EquipmentSpec / PED）
2. 双分类器独立推理（RF 二分类 + CatBoost 动态 K）
3. 概率及校准状态输出
4. 正常 / 异常初步判断（NORMAL / ABNORMAL）
5. 动态 K 类故障识别
6. 模型一致性检查（CONFLICT / UNCERTAIN）
7. 确定性诊断分流：NORMAL → l2_complete；ABNORMAL / CONFLICT / UNCERTAIN → l3_review_recommended
8. 典型频谱模式参考说明（`spectral_reference`，reference_only）
9. DiagnosisRun 持久化与版本追溯
10. L2→L3 交接契约（`auto_review` / `manual_request`）
11. 总览及 L2 Workspace 展示
12. 训练、激活、回滚、设备注册

## 不属于 L2（后续分层）

| 能力 | 归属 |
|---|---|
| 故障严重度、位置分类 | L3+ |
| 根因证据与知识推理 | L4 |
| 建议措施、优先级、业务影响 | L3–L5 |
| 原始信号频谱验证 / FFT | L3（需原始波形） |

更深算法放入 L3；根因放入 L4；结果交付与反馈放入 L5。

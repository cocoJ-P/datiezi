# L2 Training Core v1

本地双分类器离线训练内核：RandomForest（正常/非正常）+ CatBoost（五类故障），含概率校准、评测与可重载模型包。

## 数据

开发适配输入：

- `features.csv`（含 `记录编号` + 12 个数值特征）
- `labels.csv`（含 `记录编号` + `故障类型`，可选设备编号/时间/设备状态）

示例已导出到仓库：

`data/l2_dev/main_mill_motor/`

## 训练

在 `backend` 目录：

```bash
python -m l2.training.cli train ^
  --config l2/training/config/main_mill_motor.yaml ^
  --features ../data/l2_dev/main_mill_motor/features.csv ^
  --labels ../data/l2_dev/main_mill_motor/labels.csv ^
  --output-root ../models
```

其他命令：

```bash
python -m l2.training.cli verify-bundle --bundle ../models/main_mill_motor/<run_id>
python -m l2.training.cli evaluate --bundle ... --features ... --labels ...
python -m l2.training.cli --help
```

## 推理入口（供后续 Inference Service）

```python
from l2.training import load_model_bundle, predict_bundle

bundle = load_model_bundle(path)
predict_bundle(bundle, [{...12 features...}])
```

## 测试

```bash
python -m pytest l2/training/tests -q
```

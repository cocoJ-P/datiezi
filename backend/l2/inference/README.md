# L2 Inference Service v1

复用 Training Core 的 `load_model_bundle` / `predict_bundle`，挂载到现有 FastAPI。

## 启动（PowerShell）

```powershell
cd D:\钢铁诊断\steel-diagnosis-agent-main\backend
$env:STARTUP_TOOL_SELFTEST="0"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- API 文档：http://127.0.0.1:8000/docs
- Health：http://127.0.0.1:8000/l2/health
- Model：http://127.0.0.1:8000/l2/model

## 路由

- `GET /l2/health`
- `GET /l2/model`
- `POST /l2/diagnose`
- `POST /l2/diagnose/batch`

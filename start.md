# 本地启动说明

前后端启动方式**没有改**：仍是两个终端分别启动，无 Docker 一键启动。

| 服务 | 地址 |
|------|------|
| 前端（浏览器打开这个） | http://localhost:5173 |
| 后端 API / 文档 | http://127.0.0.1:8000 / http://127.0.0.1:8000/docs |

前端会把 `/api`、`/l2` 代理到后端，日常只用打开 **5173**。

---

## 首次准备（只需做一次）

在项目根目录（能同时看到 `frontend`、`backend` 的那一层）打开 PowerShell：

```powershell
# 1. API Key
Copy-Item backend\.env.example backend\.env
# 编辑 backend\.env，填写 DEEPSEEK_API_KEY=你的密钥

# 2. 后端依赖
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..

# 3. 前端依赖
cd frontend
npm install
cd ..
```

---

## 日常启动（两个终端）

**终端 1 — 后端**

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**终端 2 — 前端**

```powershell
cd frontend
npm run dev
```

浏览器打开：http://localhost:5173

两个终端都不要关。停服务：在对应终端按 `Ctrl + C`。

---

## 快速自检

1. 后端：浏览器能打开 http://127.0.0.1:8000/docs  
2. 前端：http://localhost:5173 能进系统界面  
3. 页面接口异常时，先确认后端终端是否还在跑  

---

## 补充说明（一般不用管）

- L2 训练 Worker 默认随 `uvicorn` 同进程自动启动，**不必**另开终端。
- 改过 `frontend/vite.config.ts` 代理后，需要重启 `npm run dev`。
- 缺官方数据包时服务仍可启动，但监测/图谱/诊断等业务功能会受限（见 `data/README.md`）。
- 更细的老板操作文档：`docs/项目下载与本地运行指南.md`

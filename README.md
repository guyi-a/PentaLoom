# PentaLoom

PentaLoom 是一个面向桌面端的多智能体助手。名字里的 Loom 是"织机"的意思 —— 五项能力各自负责一根线，由主 Agent 协调织出用户想要的结果。

## 五项能力

它包含五项能力：浏览器操作、文件管理、电脑控制、应用生成、智能搜索。每项能力都是一组独立实现的工具模块，主 Agent 负责理解用户意图、调度合适的能力、再把结果整合起来。

## 架构

当前形态：

- **Agent 层（Python / FastAPI）** 基于 Claude Agent SDK，复用它成熟的 agent loop、MCP 集成、HITL 工具授权等能力，把精力集中在五项能力本身的工程深度上。SQLite 持久化会话 + SSE 实时事件流。
- **前端（Vite + React 19 + TypeScript）** 三栏布局: 会话列表 / 主对话流 / 右侧工作区面板 (Todo / Mounted dirs / Context files)。

前端通过 `/api` 代理直连 agent，本期还没引入独立的网关 / 沙箱层。

## 快速开始

### 环境要求
- Python 3.12+
- Node.js 20+
- macOS / Linux

### 一键启动 (开发模式: agent + frontend)

```bash
git clone https://github.com/guyi-a/PentaLoom.git
cd PentaLoom

# (可选) 提前填 API key — 不填也能启动, 但调用会失败
cp agent/.env.example agent/.env

# 一键启动: 自动建 venv + 装依赖 + 起 agent + 起 frontend
./start-dev.sh
```

启动选项:
```bash
./start-dev.sh --no-frontend   # 只起 agent (调后端接口用)
./start-dev.sh --electron      # 起 agent + frontend + Electron 桌面壳
./start-dev.sh --debug         # agent 开热重载
```

启动后访问:

| URL | 用途 |
|---|---|
| http://localhost:5273 | 前端 |
| http://localhost:8090 | agent 根 |
| http://localhost:8090/docs | Swagger API 文档 |
| http://localhost:8090/health | 健康检查 |

**停止**: 在终端按 `Ctrl+C`, 脚本会自动 kill 所有子进程 (agent + frontend)。

Electron 日志写入 `logs/dev/electron.log`。如果在受限沙盒/无 GUI 环境里直接运行 Electron，可能会看到底层 `SIGABRT`；请在普通 macOS 终端里运行 `./start-dev.sh --electron` 或 `cd electron && pnpm start`。

### 手动启动 (调试用)

```bash
# agent (新开一个终端)
cd agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py                              # 跑在 8090

# frontend (再开一个终端)
cd frontend
npm install
npm run dev                                 # 跑在 5273
```

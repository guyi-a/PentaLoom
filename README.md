# PentaLoom

PentaLoom 是一个面向桌面端的多智能体助手。名字里的 Loom 是"织机"的意思 —— 五个子 Agent 各自负责一根线，协同织出用户想要的结果。

## 五个子 Agent

它包含五个子 Agent：浏览器操作、文件管理、电脑控制、应用生成、智能搜索。每个子 Agent 都是一个独立的"专家"，主 Agent 负责理解用户意图、把任务分派给合适的子 Agent，再把结果整合起来。

## 架构

技术上分两层：

- **Agent 层（Python）** 基于 Claude Agent SDK，复用它成熟的 agent loop、subagent 调度、MCP 集成等能力，把精力集中在子 Agent 本身的工程深度上。
- **后端层（Go）** 负责 API 网关、会话管理、沙箱编排、文件系统隔离，以及电脑操作所需要的原生系统调用桥接。

两层之间通过 gRPC streaming 通信。

## 快速开始

```bash
cd agent
python3 -m venv venv
source venv/bin/activate
```

## 项目定位

这个项目不以商业化为目标，而是一个深度练手的载体 —— 用来吃透多智能体架构、Claude Agent SDK 的工程实践、Go 后端工程，以及把每一个子 Agent 都做到值得拿出来讲的程度。

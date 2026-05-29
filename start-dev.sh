#!/bin/bash
# PentaLoom 开发模式一键启动 — agent (FastAPI) + frontend (Vite) 两进程
# 用法: ./start-dev.sh
# 选项:
#   --no-frontend   只起 agent (调后端接口用)
#   --debug         agent 开热重载 (PENTALOOM_DEBUG=true)

set -e

cd "$(dirname "$0")"

WITH_FRONTEND=1
DEBUG=0
FRONTEND_PORT=5273
LOG_DIR="logs/dev"
for arg in "$@"; do
  case "$arg" in
    --no-frontend) WITH_FRONTEND=0 ;;
    --debug) DEBUG=1 ;;
  esac
done

echo "🧵 启动 PentaLoom 开发环境"
echo ""
mkdir -p "$LOG_DIR"

PIDS=()

port_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

cleanup() {
  echo ""
  echo "⏹  停止所有服务..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 0.3
  for pid in "${PIDS[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done
  exit
}
trap cleanup INT TERM

# ── Agent (FastAPI) ───────────────────────────────────────────────────
echo "📡 启动 agent (FastAPI, 端口 8090)..."
pushd agent > /dev/null

if [ ! -d "venv" ]; then
  echo "  → venv 不存在, 自动创建并安装依赖..."
  python3 -m venv venv
  source venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
else
  source venv/bin/activate
fi

# .env 兜底
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "  → .env 不存在, 从 .env.example 复制 (记得填 API key)..."
  cp .env.example .env
fi

if [ "$DEBUG" = "1" ]; then
  PENTALOOM_DEBUG=true python main.py > "../${LOG_DIR}/agent.log" 2>&1 &
else
  python main.py > "../${LOG_DIR}/agent.log" 2>&1 &
fi
PIDS+=($!)
popd > /dev/null

# 等 agent /health 就绪
echo "  ⏳ 等待 http://127.0.0.1:8090/health ..."
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8090/health >/dev/null 2>&1; then
    echo "  ✓ agent 就绪"
    break
  fi
  sleep 0.5
  if [ "$i" = "60" ]; then
    echo "  ⚠ 30s 仍未就绪, 看上面 [agent] 输出排查"
  fi
done

# ── Frontend (Vite) ───────────────────────────────────────────────────
if [ "$WITH_FRONTEND" = "1" ]; then
  echo "🎨 启动前端 (Vite, 固定端口 ${FRONTEND_PORT})..."

  FRONTEND_REUSED=0
  if curl -sf "http://127.0.0.1:${FRONTEND_PORT}" >/dev/null 2>&1; then
    echo "  ✓ http://127.0.0.1:${FRONTEND_PORT} 已有前端服务, 直接复用"
    FRONTEND_REUSED=1
  elif [ -n "$(port_pids "$FRONTEND_PORT")" ]; then
    echo "  ✗ 端口 ${FRONTEND_PORT} 已被占用, 但不是可访问的前端服务"
    echo "    占用 PID: $(port_pids "$FRONTEND_PORT" | tr '\n' ' ')"
    echo "    可手动执行: lsof -nP -iTCP:${FRONTEND_PORT} -sTCP:LISTEN"
    cleanup
  fi

  if [ "$FRONTEND_REUSED" = "0" ]; then
  pushd frontend > /dev/null
  if [ ! -d "node_modules" ]; then
    echo "  → node_modules 不存在, 自动 npm install..."
    npm install --silent
  fi
  npm run dev > "../${LOG_DIR}/frontend.log" 2>&1 &
  PIDS+=($!)
  popd > /dev/null

  echo "  ⏳ 等待 http://127.0.0.1:${FRONTEND_PORT} ..."
  for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:${FRONTEND_PORT}" >/dev/null 2>&1; then
      echo "  ✓ 前端就绪"
      break
    fi
    if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then
      echo "  ✗ 前端进程已退出, 看 ${LOG_DIR}/frontend.log 排查"
      cleanup
    fi
    sleep 0.5
    if [ "$i" = "60" ]; then
      echo "  ⚠ 30s 仍未就绪, 看 ${LOG_DIR}/frontend.log 排查"
    fi
  done
  fi
fi

echo ""
echo "✅ PentaLoom 已启动"
echo ""
echo "  🔧 agent API   : http://localhost:8090"
echo "  📖 API 文档    : http://localhost:8090/docs"
if [ "$WITH_FRONTEND" = "1" ]; then
  echo "  🎨 前端        : http://localhost:${FRONTEND_PORT}"
fi
echo ""
echo "  🪵 日志目录    : ${LOG_DIR}/ (agent.log / frontend.log)"
echo "  按 Ctrl+C 停止所有服务"
echo ""

wait

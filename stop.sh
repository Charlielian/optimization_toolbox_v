#!/usr/bin/env bash
# 4G/5G小区PCI与邻区规划工具 - 停止脚本
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-4001}"

echo "============================================="
echo " 停止 PCI与邻区规划工具"
echo " 目标端口: ${PORT}"
echo "============================================="

PIDS=""

# 1) 优先通过端口查找 LISTEN 进程
if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)
fi

# 2) 兜底: 按命令行匹配 uvicorn
if [ -z "${PIDS}" ] && command -v pgrep >/dev/null 2>&1; then
  PIDS=$(pgrep -f "uvicorn main:app.*--port[[:space:]]+${PORT}" 2>/dev/null || true)
fi

if [ -z "${PIDS}" ]; then
  echo "[INFO] 未发现运行中的服务 (端口 ${PORT})"
  exit 0
fi

echo "[INFO] 命中进程: ${PIDS}"
echo "[INFO] 发送 SIGTERM ..."

# SIGTERM 可能一次需要发给多个 PID, 用数组更稳
kill -TERM ${PIDS} 2>/dev/null || true

# 等待优雅退出 (最多 5s)
WAITED=0
while [ "${WAITED}" -lt 5 ]; do
  REMAINING=""
  for p in ${PIDS}; do
    if kill -0 "${p}" 2>/dev/null; then
      REMAINING="${REMAINING} ${p}"
    fi
  done
  if [ -z "${REMAINING// /}" ]; then
    echo "[OK] 服务已停止"
    exit 0
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done

echo "[WARN] 进程未在 5s 内退出, 发送 SIGKILL ..."
kill -KILL ${PIDS} 2>/dev/null || true
sleep 1

REMAINING=""
for p in ${PIDS}; do
  if kill -0 "${p}" 2>/dev/null; then
    REMAINING="${REMAINING} ${p}"
  fi
done

if [ -n "${REMAINING// /}" ]; then
  echo "[ERROR] 停止失败, 残留 PID:${REMAINING}"
  echo "[HINT] 可手动: kill -9 ${REMAINING}"
  exit 1
fi

echo "[OK] 服务已强制停止"
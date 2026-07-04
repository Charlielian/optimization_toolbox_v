#!/usr/bin/env bash
# 4G/5G小区PCI与邻区规划工具 - 重启脚本
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-4001}"
HOST="${HOST:-0.0.0.0}"

echo "============================================="
echo " 重启 PCI与邻区规划工具"
echo " 目标端口: ${PORT}"
echo "============================================="

bash "${SCRIPT_DIR}/stop.sh"
RC=$?

# 0 = 没在跑, 1 = 停止失败, 0 = 已停
if [ "${RC}" -ne 0 ]; then
  echo "[ERROR] 停止阶段失败 (rc=${RC}), 中止重启"
  exit "${RC}"
fi

echo ""
echo "[INFO] 等待端口释放 ..."
WAITED=0
while [ "${WAITED}" -lt 5 ]; do
  if command -v lsof >/dev/null 2>&1; then
    if ! lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done

# 接管 start.sh 的 PORT/HOST, 后台运行, 不阻塞当前 shell
nohup bash "${SCRIPT_DIR}/start.sh" >"${SCRIPT_DIR}/logs/app.out" 2>&1 &
NEW_PID=$!

echo "[INFO] 已启动新进程 PID=${NEW_PID}"
echo "[INFO] 日志: ${SCRIPT_DIR}/logs/app.out"

# 简单健康检查: 最多等 10s 看端口是否监听
WAITED=0
while [ "${WAITED}" -lt 10 ]; do
  if command -v lsof >/dev/null 2>&1; then
    if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[OK] 服务已就绪: http://${HOST}:${PORT}"
      echo "[OK] 接口文档: http://${HOST}:${PORT}/docs"
      exit 0
    fi
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done

echo "[WARN] 10s 内端口 ${PORT} 仍未监听, 请查看日志: ${SCRIPT_DIR}/logs/app.out"
exit 0
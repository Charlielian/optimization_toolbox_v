#!/usr/bin/env bash
# 4G/5G小区PCI与邻区规划工具启动脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-4001}"
HOST="${HOST:-0.0.0.0}"

echo "============================================="
echo " 4G/5G PCI与邻区规划工具"
echo " 启动端口: ${PORT}"
echo "============================================="
echo ""

# 检查依赖
python3 -c "import fastapi, uvicorn, pandas, shapely, openpyxl" 2>/dev/null || {
  echo "[INFO] 缺少依赖,正在安装..."
  pip install -r requirements.txt
}

# 启动后端
echo ""
echo "[INFO] 启动FastAPI服务: http://${HOST}:${PORT}"
echo "[INFO] 接口文档: http://${HOST}:${PORT}/docs"
echo ""

cd "$SCRIPT_DIR/backend"
exec python3 -m uvicorn main:app --host "$HOST" --port "$PORT"
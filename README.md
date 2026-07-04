# 网优百宝箱 v1.2.1

轻量化、可离线部署的4G/5G小区规划工具，支持工参导入、GIS可视化、三级PCI智能规划、7:3加权邻区规划、多制式邻区生成、冲突校验、报表与MML脚本导出，适配陆地及近海超远覆盖场景。

## 技术栈

- **后端**：Python 3.9+ / FastAPI / Pandas / Shapely
- **前端**：原生 JS / Leaflet 1.9 / Turf.js / Element Plus（CDN，无构建步骤）
- **打包**：PyInstaller（可选，用于离线EXE单机部署）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
bash start.sh     # 启动 (前台)
bash stop.sh      # 停止
bash restart.sh   # 重启 (后台运行, 日志: logs/app.out)
```

浏览器自动打开 `http://localhost:8888`，即可使用。

如需自定义端口 / 绑定地址：

```bash
PORT=8888 HOST=0.0.0.0 bash start.sh
PORT=8888 HOST=0.0.0.0 bash stop.sh
PORT=8888 HOST=0.0.0.0 bash restart.sh
```

### 3. 使用流程

1. **上传工参**：点击"上传工参"按钮，选择 `static/sample_cells.xlsx` 或自有Excel/CSV
2. **配置参数**：左侧面板调整最大邻区数、最大距离、距离/交叠权重等
3. **执行规划**：点击"全网规划"按钮，后端执行三级PCI+邻区规划
4. **查看结果**：中间地图渲染扇区、邻区连线、冲突标红；右侧面板查看详情
5. **校验冲突**：点击"冲突校验"获取冲突清单，支持一键修复
6. **导出结果**：导出新工参表、冲突报表、邻区清单、MML脚本

## 目录结构

```
规划工具/
├── backend/            # 后端FastAPI服务
│   ├── main.py         # 入口与RESTful接口
│   ├── models.py       # Pydantic模型
│   ├── geo_utils.py    # Vincenty距离/扇区/交叠
│   ├── data_parser.py  # 工参导入清洗
│   ├── conflict_check.py
│   ├── pci_planner.py
│   ├── nbr_planner.py
│   └── exporter.py
├── frontend/           # 前端Leaflet页面
│   ├── index.html
│   ├── css/style.css
│   └── js/{app,map,api}.js
├── static/             # 示例数据
├── temp/               # 规划结果缓存
├── requirements.txt
├── start.sh
├── stop.sh
├── restart.sh
└── README.md
```

## 接口文档

启动后访问 `http://localhost:8888/docs` 查看FastAPI自动生成的接口文档。

主要接口：

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/upload` | 上传工参文件 |
| POST | `/api/plan/all` | 全网PCI+邻区规划 |
| POST | `/api/plan/partial` | 局部微调规划 |
| POST | `/api/check/conflict` | 冲突校验 |
| POST | `/api/export/file` | 导出工参/报表/MML |
| GET  | `/api/cells` | 获取当前小区列表 |
| POST | `/api/plan/single` | 单站规划 |
| POST | `/api/plan/batch` | 批量规划 (multipart, 直接返回 xlsx) |
| POST | `/api/export/split` | 导出 PCI 规划表 + 按邻区类型分 sheet |
| POST | `/api/interference/analyze` | 扇区干扰分析 (同频/邻频/PCI 三检) |
| POST | `/api/interference/export` | 导出干扰分析报告 xlsx |

## 默认端口

- 单端口模式：`4001`（同时托管前端 + API）
- 自定义：`PORT=8888 bash start.sh`

## 工参字段说明

| 字段 | 说明 | 示例 |
|---|---|---|
| ECGI | 小区全局标识 | 460-00-12345-1 |
| 小区名称 | 站点名-扇区号 | BJ001-1 |
| 制式 | LTE / NR | LTE |
| 频点 | 频段号或中心频率 | 1850 |
| 经度 | WGS84 | 116.404 |
| 纬度 | WGS84 | 39.915 |
| 方位角 | 正北方向，顺时针0-360 | 0 |
| 覆盖半径 | 米 | 500 |
| TAC | 跟踪区 | 12345 |
| PCI | 物理小区ID | 100 |
| 站点类型 | 陆地/近海/室内 | 陆地 |

## 单站规划 / 批量规划 / 干扰分析 (新功能)

### 站点类型与 PCI 距离阈值

`plan_site_type` 字段把站点类型归一化为三分类，与 `site_type` 并存：

| 标签 | 代码 | safe_distance (m) | same_pci_min (m) | 说明 |
|---|---|---|---|---|
| 宏站 | `macro`  | 700 | 5000 | 700m 内强制 mod3 不同，5km 内不同 PCI |
| 微站 | `micro`  | 200 | 3000 | 200m 内强制 mod3 不同，3km 内不同 PCI |
| 室分 | `indoor` | 100 | 2000 | 100m 内强制 mod3 不同，2km 内不同 PCI |

阈值与全局默认（1500m/30000m）取并集（更严者）。

### 新增接口

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/plan/single` | 单站规划（输入经纬度+扇区数+频段+规划类型，聚焦 10km 渲染） |
| POST | `/api/plan/batch`  | 批量规划（multipart 上传 xlsx，后台规划 + 直接返回多 sheet xlsx） |
| POST | `/api/export/split`| 导出 PCI规划表 + 邻区按规划类型分 sheet 的 xlsx |
| POST | `/api/interference/analyze` | 干扰分析 (同频/邻频/PCI三检)，返回 issues + mitigation |
| POST | `/api/interference/export`   | 导出干扰分析报告 xlsx |

### 单站规划请求示例

```json
POST /api/plan/single
{
  "lat": 22.0,
  "lon": 113.0,
  "rat": "5G",
  "freq_band": "2.6G",
  "plan_site_type": "macro",
  "n_sectors": 3,
  "base_azimuth": 0,
  "nbr_plan_types": ["4G_4G", "4G_5G", "5G_4G", "5G_5G"],
  "engine": "legacy",
  "reuse_distance_km": 5.0,
  "check_mod6": false,
  "check_mod30": true,
  "use_beam_overlap_score": false
}
```

返回：
- `planned_cells`: 展开后的 N 个小区 (含 new_pci)
- `nbr_by_type`: 按 4G_4G / 4G_5G / 5G_4G / 5G_5G 分组的邻区列表
- `nbr_counts`: 每种类型的邻区数量
- `center`: 中心点 `{lat, lon, rat, freq_band, plan_site_type}`

### 批量规划请求示例

```
POST /api/plan/batch (multipart/form-data)
file: <xlsx>
nbr_plan_types: 4G_4G,4G_5G,5G_4G,5G_5G
engine: legacy
reuse_distance_km: 5.0
check_mod6: false
check_mod30: true
```

上行上限 500 行，超出截断并写入日志告警。

### 干扰分析请求示例

```json
POST /api/interference/analyze
{
  "interference_distance_km": 5.0,
  "overlap_threshold": 30,
  "detect_co_channel": true,
  "detect_adjacent_channel": true,
  "detect_pci_collision": true,
  "detect_mod3": true,
  "detect_mod6": false,
  "center_ecgi": "460-00-123456-101",
  "radius_km": 10.0
}
```

返回：
- `issues`: 干扰列表 (类型 / 严重度 / 距离 / overlap / 详情)
- `stats`: 各类干扰计数 + 严重度分级
- `mitigation`: RFTools 风格的 mitigation 建议

### 扩展工参字段

下载工参模板 (`/api/template?rat=4G/5G/both`) 时会多出以下列：

| 字段 | 必填 | 说明 |
|---|---|---|
| 扇区数 | 否 | 1-6，默认 1 |
| 基方位角 | 否 | 0-360，默认 0 |
| 规划类型 | 否 | 宏站/微站/室分；不填则按站点类型自动归类 |
| 邻区规划 | 否 | 枚举组合，多选用 `|` 分隔：`4G_4G`/`4G_5G`/`5G_4G`/`5G_5G` |
| 锁定 | 否 | 是/否；锁定小区不参与 PCI 重分配 |

模板新增两个说明 sheet：`规划类型说明` 和 `邻区规划类型说明`。

### 算法引擎

- **legacy** (默认): 地理分簇贪心打分 (`pci_planner.py`)
- **rftools**: RFTools 顺序递增+空间索引+Mod3/Mod6/Mod30 三检 (`pci_rsi_planner.py`)

切换方式：前端 `PCI 规划 → 算法引擎` 单选框 + `Mod6` / `Mod30` 勾选框。

### RFTools 算法参考

移植自开源仓库 [mbebs/RFTools](https://github.com/mbebs/RFTools)：

- `pci_rsi_planner_dialog.py` → `backend/pci_rsi_planner.py` (PCI 顺序递增 + 空间索引)
- `interference_analysis_dialog.py` → `backend/interference_analysis.py` (扇区夹角判断 + 同频/邻频/PCI 三检 + Mitigation)

差异说明：
- 距离从 QGIS `QgsDistanceArea` 替换为 `geo_utils.vincenty_distance`（WGS84 椭球）
- 几何坐标从 QGIS `QgsPointXY` 替换为 `(lat, lon)` tuple
- 字段命名：tech→rat, band→freq_band
- 新增 Mod30 (NR DMRS) 检查，与原 Mod3/Mod6 共存并可独立勾选

### 新增/修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `backend/site_type_ext.py`     | 新建 | 站点类型扩展 + PCI 阈值表 |
| `backend/pci_rsi_planner.py`   | 新建 | RFTools PCI 贪心算法移植 |
| `backend/interference_analysis.py` | 新建 | RFTools 干扰分析三检 + Mitigation |
| `backend/site_planner.py`      | 新建 | 单站/批量规划主流程 |
| `backend/pci_planner.py`       | 修改 | `greedy_allocate` / `plan_all` 增加 `engine`/`per_site_thresholds`/`check_mod30` 参数 |
| `backend/nbr_planner.py`       | 修改 | `plan_neighbors` 增加 `nbr_plan_types` / `use_beam_overlap_score` |
| `backend/data_parser.py`       | 修改 | 解析 `n_sectors`/`base_azimuth`/`plan_site_type`/`nbr_plan_types`/`locked` |
| `backend/template_generator.py`| 修改 | 模板新增 5 列 + 规划类型/邻区类型说明 sheet |
| `backend/exporter.py`          | 修改 | 新增 `export_plan_split_sheets` + `export_interference_report` |
| `backend/main.py`              | 修改 | 新增 5 个 API 端点 |
| `backend/db.py`                | 修改 | `_CELL_COLUMNS` 新增 5 列 + 兼容迁移 |
| `backend/sector_params.py`     | 修改 | 新增 `micro` 分支（40°/40m），修正 `offdoor→offshore` |
| `frontend/index.html`          | 修改 | 新增 3 个 section (单站/批量/干扰分析) + 算法引擎切换 |
| `frontend/js/map.js`           | 修改 | 新增 `focusedCells` / `renderNeighborsByType` / `renderInterference` |
| `frontend/js/app.js`           | 修改 | 新增 `planSingle` / `planBatchAndExport` / `analyzeInterference` 等 |
| `frontend/js/api.js`           | 修改 | 新增 5 个 API 调用封装 |

## 打包离线EXE（可选）

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "../frontend:frontend" --add-data "../static:static" backend/main.py
```
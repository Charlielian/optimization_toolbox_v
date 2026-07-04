// Leaflet地图管理 - 扇区渲染版
// 核心变更: 加载即渲染扇区多边形(圆弧扇形)，4G/5G 分层，冲突标红，邻区打分队形
const MapManager = {
  map: null,

  // ── 图层 ──────────────────────────────────────────────
  layers: {
    lteSectors:  null,   // 4G 扇区面 (独立图层, 可勾选)
    nrSectors:   null,   // 5G 扇区面 (独立图层, 可勾选)
    plannedSites: null,   // 规划小区扇区
    azimuthLines: null,   // 方位线
    cellPoints:   null,   // 基站圆点
    conflicts:    null,   // 冲突高亮
    neighbors:    null,    // 邻区连线
    interference: null,    // 干扰连线
    selection:    null,    // 框选
    pciLabels4G:  null,   // 4G PCI 标签 (按 mod3 着色)
    pciLabels5G:  null,   // 5G PCI 标签 (按 mod3 着色)
  },

  sectorCache:  new Map(),  // ecgi → sector layer group
  cellIndex:    new Map(),  // ecgi → cell data
  conflictCache: new Set(),  // 当前冲突 ecgi 集合

  // 缓存当前渲染状态（用于滑块变化时重渲染）
  _lastCells:   [],
  _lastConflictEcgis: new Set(),
  _lastPlannedEcgis: new Set(),

  // PCI 标签显示的最低 zoom (低于此值时不生成 divIcon, 避免 3 万级 DOM 卡顿)
  PCI_LABEL_MIN_ZOOM: 12,

  // 视觉半径（米）：用于扇区显示与邻区连线端点偏移
  visualRadius: 300,

  // ── 配色 ──────────────────────────────────────────────
  RAT_COLORS: {
    'LTE': '#1d6fcc',
    'NR':  '#7c3aed',
  },

  // 4G 扇区填充色 (蓝色系, 半透明)
  LTE_FILL_COLOR:  '#1d6fcc',
  LTE_FILL_OPACITY: 0.22,

  // 5G 扇区填充色 (紫色系, 半透明)
  NR_FILL_COLOR:   '#7c3aed',
  NR_FILL_OPACITY: 0.22,

  // mod3 调色板: 扇区填充色按 PCI % 3 决定 (4G/5G 统一使用, 一眼看出同 mod3 簇)
  //   mod3=0 → 红    mod3=1 → 黄    mod3=2 → 蓝
  MOD3_FILL_PALETTE: {
    0: { color: '#ff6b6b', fill: '#ff6b6b', opacity: 0.42, cssClass: 'mod3-0' },
    1: { color: '#ffd43b', fill: '#ffd43b', opacity: 0.42, cssClass: 'mod3-1' },
    2: { color: '#4dabf7', fill: '#4dabf7', opacity: 0.42, cssClass: 'mod3-2' },
  },
  // 没有有效 PCI (例如工参缺失) 时的兜底色调
  MOD3_FILL_UNKNOWN: { color: '#9aa0b0', fill: '#9aa0b0', opacity: 0.18, cssClass: 'unset' },

  // 冲突色
  CONFLICT_COLOR:   '#dc2626',
  CONFLICT_FILL:    '#dc2626',

  // 邻区连线
  NEIGHBOR_COLOR:   '#16a34a',

  // 规划小区配色 (按站点类型)
  PLAN_COLORS: {
    'macro':  '#facc15',
    'micro':  '#fb923c',
    'indoor': '#fde047',
  },

  // 邻区连线配色
  NBR_TYPE_COLORS: {
    '4G_4G': '#2563eb',
    '4G_5G': '#10b981',
    '5G_4G': '#ef4444',
    '5G_5G': '#8b5cf6',
  },

  // 干扰连线配色
  INTERFERENCE_COLORS: {
    'Co-Channel':       '#dc2626',
    'Adjacent Channel': '#f59e0b',
    'PCI Conflict':     '#7c3aed',
  },

  // ── 内部状态 ──────────────────────────────────────────────
  _focusMode:    false,
  _focusCenter:   null,
  _focusRadiusKm: 0,

  // 扇区弧线采样点数 (数值越大弧越平滑)
  _SECTOR_N: 36,

  // 扇区绘制半径 (米)
  // - CASE_VISUAL_MAX_M: CASE 半径上限, 超过此值的扇区会被截断 (防异常半径泄漏)
  // - VISUAL_SCALE: 绘制缩放系数 (符号比例尺, 让 zoom 12-13 能看清)
  // 注意: 邻区连线端点几何必须用同一公式, 否则端点会落在扇区"中间"而非"最高点"
  CASE_VISUAL_MAX_M: 60,
  VISUAL_SCALE: 2.5,

  // 扇区面样式模板
  _getSectorStyle(rat, isConflict, isPlanned, planType, mod3Class) {
    let fillColor, fillOpacity, color, weight;
    if (isConflict) {
      // 冲突最高优先级: 红色覆盖 mod3 色, 一眼锁定
      color = this.CONFLICT_COLOR;
      fillColor = this.CONFLICT_FILL;
      fillOpacity = 0.45;
      weight = 2;
    } else if (isPlanned) {
      // 规划小区: 填充按 planType (macro/micro/indoor), 边框用 mod3 色提示冲突簇
      color = this.PLAN_COLORS[planType] || '#facc15';
      fillColor = color;
      fillOpacity = 0.45;
      weight = 2.5;
    } else if (mod3Class && this.MOD3_FILL_PALETTE[mod3Class]) {
      // 普通小区: 完全按 mod3 填色 (替换原 RAT 蓝/紫)
      const palette = this.MOD3_FILL_PALETTE[mod3Class];
      color = palette.color;
      fillColor = palette.fill;
      fillOpacity = palette.opacity;
      weight = 1.5;
    } else {
      // PCI 缺失/无效: 灰色兜底
      const palette = this.MOD3_FILL_UNKNOWN;
      color = palette.color;
      fillColor = palette.fill;
      fillOpacity = palette.opacity;
      weight = 1;
    }
    return { color, fillColor, fillOpacity, weight, mod3Class: mod3Class || null };
  },

  // ── 提取 cell 的 mod3 分类 ──────────────────────────────────────────────
  /**
   * 从 cell 读 PCI (优先 new_pci, 否则 pci), 计算 mod3 (0/1/2)
   * 缺失/无效返回 null
   */
  _cellMod3(cell) {
    const raw = cell.new_pci != null ? cell.new_pci : cell.pci;
    if (raw == null || raw === '' || parseInt(raw, 10) < 0) return null;
    const p = parseInt(raw, 10);
    if (!isFinite(p)) return null;
    return p % 3;
  },

  // ── 初始化 ──────────────────────────────────────────────
  init(mapElementId) {
    this.map = L.map(mapElementId, {
      center: [21.858, 111.955],
      zoom: 11,
      zoomControl: false,
      preferCanvas: true,
    });

    // 底图1: ArcGIS 卫星
    const arcgisTiles = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: '© Esri, Maxar, Earthstar Geographics',
        maxZoom: 19,
        crossOrigin: true,
      }
    );

    // 底图2: Google 卫星
    const googleTiles = L.tileLayer(
      'https://gac-geo.googlecnapps.club/maps/vt?lyrs=s&x={x}&y={y}&z={z}&src=app&scale=2&from=app',
      {
        attribution: '© Google',
        maxZoom: 21,
        maxNativeZoom: 18,
        tileSize: 512,
        zoomOffset: -1,
        detectRetina: false,
        crossOrigin: true,
      }
    );

    // 底图3: OSM
    const osmTiles = L.tileLayer(
      'https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png',
      {
        subdomains: ['a', 'b', 'c'],
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19,
        crossOrigin: true,
      }
    );

    // 底图4: 暗色大屏 (CartoDB Dark Matter) — 默认
    const darkTiles = L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      {
        subdomains: ['a', 'b', 'c', 'd'],
        attribution: '© CartoDB © OpenStreetMap contributors',
        maxZoom: 19,
        crossOrigin: true,
      }
    );

    // ArcGIS 卫星底图设为默认
    arcgisTiles.addTo(this.map);

    // ArcGIS 卫星加载失败时降级到 Google 卫星 (仅当使用卫星底图时)
    arcgisTiles.on('tileerror', () => {
      if (this._fallbackAdded) return;
      if (this.map.hasLayer(arcgisTiles) && !this.map.hasLayer(googleTiles)) {
        this._fallbackAdded = true;
        googleTiles.addTo(this.map);
        this.map.removeLayer(arcgisTiles);
        if (window.App && window.App.log) {
          window.App.log('ArcGIS 底图加载失败, 已自动降级到 Google 卫星', 'warn');
        }
      }
    });

    // 底图切换
    L.control.layers(
      {
        '🌙 暗色大屏': darkTiles,
        '🛰️ ArcGIS 卫星': arcgisTiles,
        '🛰️ Google 卫星': googleTiles,
        '🗺️ OpenStreetMap': osmTiles,
      },
      null,
      { position: 'topright', collapsed: false }
    ).addTo(this.map);

    // ── 创建独立图层 ──────────────────────────────────────
    this.layers.lteSectors  = L.layerGroup().addTo(this.map);
    this.layers.nrSectors   = L.layerGroup().addTo(this.map);
    this.layers.plannedSites= L.layerGroup().addTo(this.map);
    this.layers.azimuthLines= L.layerGroup().addTo(this.map);
    this.layers.cellPoints  = L.layerGroup().addTo(this.map);
    this.layers.conflicts   = L.layerGroup().addTo(this.map);
    this.layers.neighbors   = L.layerGroup().addTo(this.map);
    this.layers.interference= L.layerGroup().addTo(this.map);
    this.layers.selection   = L.layerGroup().addTo(this.map);
    this.layers.pickMarker  = L.layerGroup().addTo(this.map);
    // PCI 标签图层 — 默认不开启, 用户勾选后显示
    this.layers.pciLabels4G  = L.layerGroup();  // 不 addTo, 默认隐藏
    this.layers.pciLabels5G  = L.layerGroup();

    // ── 区域圈选 状态 ─────────────────────────────────
    this._drawMode = null;     // 'rect' | 'circle' | null
    this._drawStart = null;    // mousedown latlng
    this._drawShape = null;    // 临时图形
    this._currentArea = null;  // 已完成图形 {type, ...}
    this._areaCallback = null;
    // 阻止圈选时弹出 contextmenu 取点
    this._suppressContext = false;

    // ── Zoom 滑块控件 ──────────────────────────────────────
    this._buildZoomControl();

    // ── 扇区图层切换控件 ─────────────────────────────────
    this._buildSectorLayerControl();

    // ── 右键取点 → 写入「单站规划」经纬度 ─────────────────
    this._bindPickLatLng();

    return this.map;
  },

  /**
   * 绑定右键 (contextmenu) 取点:
   *   - 阻止浏览器默认菜单
   *   - 把 e.latlng 写入 #ss-lon / #ss-lat (6 位小数, 与输入框 step 匹配)
   *   - 落点处画一个醒目的临时标记 (只保留最近一个)
   *   - 写一行 success 日志, 便于确认
   */
  _bindPickLatLng() {
    this.map.on('contextmenu', (e) => {
      if (e.originalEvent && typeof e.originalEvent.preventDefault === 'function') {
        e.originalEvent.preventDefault();
      }

      const lat = e.latlng.lat;
      const lon = e.latlng.lng;

      const lonInput = document.getElementById('ss-lon');
      const latInput = document.getElementById('ss-lat');
      if (lonInput) lonInput.value = lon.toFixed(6);
      if (latInput) latInput.value = lat.toFixed(6);

      if (this.layers.pickMarker) this.layers.pickMarker.clearLayers();
      const pulse = L.circleMarker([lat, lon], {
        radius: 8,
        color: '#facc15',
        weight: 3,
        fillColor: '#facc15',
        fillOpacity: 0.6,
      }).addTo(this.layers.pickMarker);
      pulse.bindTooltip(
        `已取点<br>经度 ${lon.toFixed(6)}<br>纬度 ${lat.toFixed(6)}`,
        { direction: 'top', offset: [0, -8], sticky: false }
      ).openTooltip();

      if (window.App && window.App.log) {
        window.App.log(`[地图取点] 经度=${lon.toFixed(6)} 纬度=${lat.toFixed(6)} 已填入「单站规划」`, 'success');
      }
    });
  },

  // ── Zoom 滑块控件 ─────────────────────────────────────────────
  _buildZoomControl() {
    const minZ = this.map.getMinZoom();
    const maxZ = this.map.getMaxZoom();
    const curZ = this.map.getZoom();

    const panel = L.control({ position: 'topright' });
    panel.onAdd = () => {
      const el = L.DomUtil.create('div', 'leaflet-zoom-slider-ctrl');
      L.DomEvent.disableClickPropagation(el);
      el.innerHTML = `
        <div class="zslider-wrap">
          <div class="zslider-row">
            <span class="zlabel">−</span>
            <input type="range" class="zslider" id="zoom-slider"
              min="${minZ}" max="${maxZ}" value="${curZ}" step="1" />
            <span class="zlabel">+</span>
          </div>
          <div class="zslider-val" id="zoom-val">Z ${curZ}</div>
        </div>
      `;

      const slider = el.querySelector('#zoom-slider');
      const valEl  = el.querySelector('#zoom-val');

      slider.addEventListener('input', (e) => {
        const z = parseInt(e.target.value, 10);
        this.map.setZoom(z);
        valEl.textContent = `Z ${z}`;
      });

      this.map.on('zoomend', () => {
        const z = this.map.getZoom();
        slider.value = z;
        valEl.textContent = `Z ${z}`;
        // PCI 标签按 zoom 阈值显示: zoom>=12 才生成, zoom<12 隐藏/不渲染
        this._refreshPciLabelsForZoom();
      });

      return el;
    };
    panel.addTo(this.map);
  },

  // ── 扇区图层切换控件 ───────────────────────────────────────────
  _buildSectorLayerControl() {
    if (this._sectorLayerControl) {
      this.map.removeControl(this._sectorLayerControl);
    }
    const overlays = {
      '📡 4G LTE 扇区':  this.layers.lteSectors,
      '📡 5G NR 扇区':   this.layers.nrSectors,
      '🔢 4G PCI 标签':  this.layers.pciLabels4G,
      '🔢 5G PCI 标签':  this.layers.pciLabels5G,
      '⭐ 规划小区':      this.layers.plannedSites,
      '📍 基站圆点':      this.layers.cellPoints,
    };
    this._sectorLayerControl = L.control.layers(null, overlays, {
      position: 'topright',
      collapsed: false,
    }).addTo(this.map);
  },

  // ── 核心渲染: 全量小区 ──────────────────────────────────────────
  /**
   * 渲染全部小区扇区
   * @param {Array} cells            小区列表
   * @param {Set}   conflictEcgis   冲突小区 ecgi 集合
   */
  renderCells(cells, conflictEcgis = new Set(), plannedEcgis = new Set()) {
    // 缓存状态（供滑块重渲染使用）
    this._lastCells = cells;
    this._lastConflictEcgis = conflictEcgis instanceof Set ? conflictEcgis : new Set(conflictEcgis || []);
    this._lastPlannedEcgis = plannedEcgis instanceof Set ? plannedEcgis : new Set(plannedEcgis || []);

    this._clearAllLayers();
    this.cellIndex.clear();
    this.sectorCache.clear();
    this.conflictCache = this._lastConflictEcgis;

    cells.forEach(c => {
      this.cellIndex.set(c.ecgi, c);
    });

    cells.forEach(c => {
      this._renderCellSector(c, new Set());
      this._renderPciLabel(c, new Set());
    });

    this._buildSectorLayerControl();
  },

  /**
   * 聚焦渲染: 单站/批量规划场景
   * @param {Array}  allCells       全部小区
   * @param {Object} center         {lat, lon} 规划中心
   * @param {Number} radiusKm       显示半径 km
   * @param {Set}    plannedEcgis  规划小区 ecgi 集合
   */
  focusedCells(allCells, center, radiusKm, plannedEcgis = new Set(), options = {}) {
    this._focusMode     = true;
    this._focusCenter  = center;
    this._focusRadiusKm = radiusKm;
    const plannedSet = plannedEcgis instanceof Set ? plannedEcgis : new Set(plannedEcgis || []);
    const { keepView = false } = options;

    this._clearAllLayers();
    this.cellIndex.clear();
    this.sectorCache.clear();
    this.conflictCache = new Set();

    // 缓存状态（供滑块重渲染使用）
    this._lastCells = allCells;
    this._lastConflictEcgis = new Set();

    allCells.forEach(c => {
      this.cellIndex.set(c.ecgi, c);
    });

    const visible = allCells.filter(c => {
      if (!c.lat || !c.lon) return false;
      const d = this._haversineKm(center.lat, center.lon, c.lat, c.lon);
      return d <= radiusKm;
    });

    // 仅当 keepView=false 时调整 map zoom (单站规划连点时保留用户当前视图)
    if (center && !keepView) {
      const viewKm = Math.max(radiusKm, 5);
      const zoom = this._calcZoomForCenter(center, viewKm);
      this.map.setView([center.lat, center.lon], zoom);
    }

    // 可见小区: 全部按普通扇区渲染到对应 RAT 图层
    visible.forEach(c => {
      this._renderCellSector(c, plannedSet, /* isFocused= */ true);
      this._renderPciLabel(c, plannedSet);
    });
    this._lastPlannedEcgis = plannedSet;

    // 规划中心 marker (在所有扇区之上)
    if (center) {
      const icon = L.divIcon({
        className: 'planned-site-center',
        html: `<div class="planned-site-pulse"></div><div class="planned-site-label">📍 规划站点</div>`,
        iconSize: [120, 40],
        iconAnchor: [60, 20],
      });
      L.marker([center.lat, center.lon], { icon, interactive: false })
        .addTo(this.layers.plannedSites);
    }

    this._buildSectorLayerControl();
  },

  // ── 单个小区扇区渲染 ────────────────────────────────────────────
  _renderCellSector(cell, plannedEcgis, isFocused = false) {
    const ecgi = cell.ecgi;
    const isPlanned = plannedEcgis.has(ecgi);
    const isConflict = this.conflictCache.has(ecgi);
    const rat = cell.rat;

    const lat = parseFloat(cell.lat);
    const lon = parseFloat(cell.lon);
    if (!isFinite(lat) || !isFinite(lon)) return;

    const az   = parseFloat(cell.azimuth) || 0;
    const bw   = parseFloat(cell.beamwidth || cell.beam) || 65;
    const currentZoom = this.map ? this.map.getZoom() : 15;

    // 规划小区 zoom 适配: 当 zoom 较小时, 用更大的视觉半径让用户能看到自己刚规划的小区
    // 普通小区用 CASE 映射值 (40m 左右), 规划小区在 zoom<13 时改用 coverage_radius (700m 等)
    // 但限制最大 200m 防遮挡邻居
    const caseR = parseFloat(cell.radius);
    const coverR = parseFloat(cell.coverage_radius);
    const rBase = (isFinite(caseR) && caseR > 0)
      ? Math.min(caseR, this.CASE_VISUAL_MAX_M)
      : 40;  // CASE 缺失兜底 40m

    let r;
    if (isPlanned && isFinite(coverR) && coverR > 0 && currentZoom < 13) {
      // 规划小区 + zoom 较远: 用 coverage_radius, 限最大 200m
      r = Math.min(coverR, 200) * (currentZoom < 11 ? 1.5 : 1);
    } else {
      // 普通小区 或 zoom 足够近: 用 CASE 视觉半径
      r = rBase * this.VISUAL_SCALE;
    }

    // zoom < 11: 只画基站圆点（多边形无意义, 此时 cell.radius 仍是判定上限）
    // zoom >= 11: 渲染扇区多边形 + 方位线 + 圆点
    // ⚠ 规划小区强制显示扇区多边形 (即使 zoom<11), 否则用户看不到自己刚规划的结果
    const showSectorPolygons = currentZoom >= 11 || isPlanned;

    // 扇区最大跨度验证: 若扇区半径对应的度数超过 0.3° (≈33km) - 异常小区
    // 此时改用三角形箭头模式 (从扇区圆心指向)
    const rDeg = r / 111000;
    const useTriangleArrow = rDeg > 0.3;

    // 计算扇区边界方位角（供三角形箭头使用）
    const halfBw = bw / 2;
    const start = ((az - halfBw) % 360 + 360) % 360;
    const end  = ((az + halfBw) % 360 + 360) % 360;

    const style = this._getSectorStyle(rat, isConflict, isPlanned, cell.plan_site_type, this._cellMod3(cell));

    let polyLayer = null;
    let lineLayer = null;

    if (showSectorPolygons) {
      let polygon;
      if (useTriangleArrow) {
        // 三角形箭头: 圆心 + 扇区边缘两点（抗退化）
        const p1 = this._destPoint(lat, lon, r / 1110, start);
        const p2 = this._destPoint(lat, lon, r / 1110, end);
        polygon = [[lat, lon], p1, p2, [lat, lon]];
      } else {
        polygon = this._buildArcSectorPolygon(lat, lon, az, bw, r / 1000);
      }
      // fillOpacity: 普通扇区按 zoom 自适应
      //   - zoom<13: 0.25 (浅一些, 避免密集城区遮挡邻区连线)
      //   - zoom>=13: 0.42 (mod3 颜色饱满)
      // 规划/冲突扇区: 用 style.fillOpacity (醒目)
      let fillOpacity;
      if (isPlanned || isConflict) {
        fillOpacity = style.fillOpacity;
      } else if (currentZoom >= 13) {
        fillOpacity = 0.42;
      } else if (currentZoom >= 12) {
        fillOpacity = 0.32;
      } else {
        fillOpacity = 0.20;
      }
      polyLayer = L.polygon(polygon, {
        color:      style.color,
        weight:     style.weight,
        fillColor:  style.fillColor,
        fillOpacity: fillOpacity,
        className:  isConflict ? 'sector-conflict' : '',
      });

      // 扇区Tooltip: 标注小区关键属性 + mod3 颜色块
      const ratZh   = { LTE: '4G', NR: '5G' }[rat] || rat;
      const pciText = cell.new_pci ?? cell.pci ?? '—';
      const conflictTag = isConflict
        ? `<span style="color:#dc2626;font-weight:bold;">⚠ 冲突</span>`
        : '';
      const plannedTag = isPlanned
        ? `<span style="color:#facc15;font-weight:bold;">⭐</span>`
        : '';
      // mod3 色块: 用一个 inline 圆点表示当前扇区颜色 (跟地图填色一致)
      const mod3 = this._cellMod3(cell);
      const palette = (mod3 != null)
        ? this.MOD3_FILL_PALETTE[mod3]
        : this.MOD3_FILL_UNKNOWN;
      const mod3Chip = `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${palette.fill};border:1px solid rgba(255,255,255,0.4);vertical-align:middle;margin-right:4px;"></span>mod3=${mod3 != null ? mod3 : '?'}`;

      polyLayer.bindTooltip(
        `<b>${cell.name || ecgi}</b> ${plannedTag} ${conflictTag}<br>` +
        `${ratZh} · ${cell.freq_band_label || cell.freq_band || '—'} · ${cell.site_type_label || cell.site_type || '—'}<br>` +
        `PCI: <span style="color:#67c23a;font-weight:bold;">${pciText}</span> · ${mod3Chip}<br>` +
        `<small>方位角: ${az}° | 扇区: ±${(bw/2).toFixed(1)}° / ${rBase.toFixed(0)}m</small>`,
        { direction: 'top', offset: [0, -5], sticky: false }
      );
      polyLayer.on('click', () => {
        if (window.App) window.App.showCellDetail(cell);
      });

      // 方位线 (指向扇区中心)
      const lineEnd = this._destPoint(lat, lon, r / 1000, az);
      lineLayer = L.polyline([[lat, lon], lineEnd], {
        color:   style.color,
        weight:  isPlanned ? 2.5 : 1.5,
        opacity: isPlanned ? 0.85 : 0.65,
      });
    }

    // 圆点始终渲染
    const pointLayer = L.circleMarker([lat, lon], {
      radius:    isPlanned ? 7 : 5,
      color:     style.color,
      fillColor: style.color,
      fillOpacity: 0.95,
      weight:   isPlanned ? 2.5 : 1.5,
    });

    // 根据制式/规划状态加入对应图层
    if (isPlanned) {
      if (polyLayer) polyLayer.addTo(this.layers.plannedSites);
      if (lineLayer) lineLayer.addTo(this.layers.plannedSites);
      pointLayer.addTo(this.layers.plannedSites);
    } else if (isConflict) {
      if (polyLayer) polyLayer.addTo(this.layers.plannedSites);
      if (lineLayer) lineLayer.addTo(this.layers.plannedSites);
      pointLayer.addTo(this.layers.plannedSites);
    } else if (rat === 'LTE' || rat === '4G') {
      if (polyLayer) polyLayer.addTo(this.layers.lteSectors);
      if (lineLayer) lineLayer.addTo(this.layers.azimuthLines);
      pointLayer.addTo(this.layers.cellPoints);
    } else {
      if (polyLayer) polyLayer.addTo(this.layers.nrSectors);
      if (lineLayer) lineLayer.addTo(this.layers.azimuthLines);
      pointLayer.addTo(this.layers.cellPoints);
    }
  },

  // ── PCI 标签 zoom 自适应 ──────────────────────────────────────────
  /**
   * 性能: PCI 标签只在 zoom >= PCI_LABEL_MIN_ZOOM 时显示
   * - zoom < PCI_LABEL_MIN_ZOOM: 清空标签 layer (减轻 DOM 负担)
   * - zoom >= PCI_LABEL_MIN_ZOOM: 如果 layer 是空, 用 _lastCells 重新渲染
   */
  _refreshPciLabelsForZoom() {
    if (!this.map) return;
    const z = this.map.getZoom();
    const MIN = this.PCI_LABEL_MIN_ZOOM;
    if (z < MIN) {
      // 缩放级别太低: 隐藏所有 PCI 标签
      if (this.layers.pciLabels4G) this.layers.pciLabels4G.clearLayers();
      if (this.layers.pciLabels5G) this.layers.pciLabels5G.clearLayers();
    } else {
      // zoom 够高: 如果 layer 为空 (刚才被 clear 过) 则重渲染
      const plannedSet = this._lastPlannedEcgis || new Set();
      if (this.layers.pciLabels4G && this.layers.pciLabels4G.getLayers().length === 0
          && this.layers.pciLabels5G && this.layers.pciLabels5G.getLayers().length === 0
          && this._lastCells && this._lastCells.length > 0) {
        this._lastCells.forEach(c => this._renderPciLabel(c, plannedSet));
      }
    }
  },

  // ── PCI 标签渲染 (divIcon, 按 mod3 着色) ────────────────────────────
  /**
   * 在扇区中心位置绘制一个 PCI 数字标签
   * - 4G → pciLabels4G, 5G → pciLabels5G
   * - 优先显示 new_pci (规划后), 否则显示 pci (工参)
   * - 按 mod3 着色: 0=红 1=黄 2=蓝
   * - 规划小区加 .planned 类 (高亮边框)
   * - PCI 缺失/无效时显示 '—' (灰色)
   */
  _renderPciLabel(cell, plannedEcgis) {
    // 性能: PCI 标签只在 zoom >= 12 时显示, 否则 divIcon 数量爆炸
    // (30448 cells 全量渲染 divIcon 会卡死 Leaflet 的 markerPane)
    if (!this.map || this.map.getZoom() < 12) return;

    const lat = parseFloat(cell.lat);
    const lon = parseFloat(cell.lon);
    if (!isFinite(lat) || !isFinite(lon)) return;

    const rat = (cell.rat || '').toUpperCase();
    const is4G = (rat === 'LTE' || rat === '4G');
    const is5G = (rat === 'NR'  || rat === '5G');
    if (!is4G && !is5G) return;  // 只画 4G/5G 标签

    // 取 PCI: 优先 new_pci (规划后), 否则 pci (工参原值)
    const rawPci = cell.new_pci != null ? cell.new_pci : cell.pci;
    const isPlanned = plannedEcgis && plannedEcgis.has(cell.ecgi);
    let pciText, mod3Class;
    if (rawPci == null || rawPci === '' || parseInt(rawPci, 10) < 0) {
      pciText = '—';
      mod3Class = 'unset';
    } else {
      const p = parseInt(rawPci, 10);
      pciText = String(p);
      mod3Class = `mod3-${p % 3}`;
    }

    const cls = ['pci-label', mod3Class, isPlanned ? 'planned' : ''].filter(Boolean).join(' ');

    // 关键: 把 PCI 数字放到"扇区中心", 而不是基站圆点位置
    // - cell.lat/lon 是基站 (site) 位置, 多个扇区共享同一点
    // - 沿 cell.azimuth 方向偏移 ~视觉半径的 50%, 让数字落在扇区弧中部
    // - 没有 azimuth 时回退到基站位置
    const az = parseFloat(cell.azimuth);
    const caseR  = parseFloat(cell.radius);
    const coverR = parseFloat(cell.coverage_radius);
    const rMeters = (isFinite(caseR) && caseR > 0)
      ? Math.min(caseR, this.CASE_VISUAL_MAX_M || 200)
      : (isFinite(coverR) && coverR > 0 ? Math.min(coverR, 200) : 40);
    const offsetM = rMeters * 0.5;  // 50% 半径处 ≈ 扇区中心
    let labelLat = lat, labelLon = lon;
    if (isFinite(az) && typeof this._destPoint === 'function') {
      const dest = this._destPoint(lat, lon, offsetM / 1000, az);
      if (Array.isArray(dest) && dest.length === 2
          && isFinite(dest[0]) && isFinite(dest[1])) {
        [labelLat, labelLon] = dest;
      }
    }

    // divIcon 容器 0×0 + CSS transform: translate(-50%, -50%) 让 div 中心对齐到 (labelLat, labelLon)
    const icon = L.divIcon({
      className: 'pci-label-wrap',
      html: `<div class="${cls}">${pciText}</div>`,
      iconSize: [0, 0],             // 不让 Leaflet 给容器加尺寸, 完全由 CSS 控制
      iconAnchor: [0, 0],           // 锚点在容器左上角 → transform translate(-50%,-50%) 让 div 中心落在 (lat,lon)
    });

    const layer = is4G ? this.layers.pciLabels4G : this.layers.pciLabels5G;
    L.marker([labelLat, labelLon], { icon, interactive: false, keyboard: false })
      .addTo(layer);
  },

  // ── 圆弧扇形多边形生成 ────────────────────────────────────────────
  /**
   * 生成圆弧扇形多边形顶点列表 (与后端 geo_utils.sector_polygon 等价)
   *
   * 几何逻辑:
   *   - 扇区中心: (lon, lat)
   *   - 覆盖半径: r (度, 由米/111000换算)
   *   - 扇区半角: ±beamwidth/2° (天线常规波瓣宽度)
   *   - 从 start_az 逆时针到 end_az 均匀采样 n 个点(含圆弧两端)
   *   - 闭合多边形: [中心点, 圆弧端点1, ... 圆弧端点N, 中心点]
   *   - 跨 0° 处理: 若扇区跨越北向(0°), 分两段弧生成
   *
   * @param {Number} lat       纬度
   * @param {Number} lon       经度
   * @param {Number} az       方位角(正北0°, 顺时针0-360)
   * @param {Number} beamwidth 波瓣全角(°)
   * @param {Number} rDeg     覆盖半径(度)
   * @returns {Array}          [[lat,lon], ...] 顶点数组
   */
  _buildArcSectorPolygon(lat, lon, az, beamwidth, rKm) {
    // 弧线采样点数 (数值越大圆弧越平滑, 36点对应每1°一个采样)
    const n = this._SECTOR_N;
    az = ((az % 360) + 360) % 360;
    const halfBw = beamwidth / 2;
    let start = ((az - halfBw) % 360 + 360) % 360;
    const end  = ((az + halfBw) % 360 + 360) % 360;

    const coords = [[lat, lon]]; // 圆心(闭合多边形起点)

    const span = beamwidth; // 总张角
    if (start + span > 360) {
      // 弧跨 0°: 分两段圆弧
      // 第一段: start → 360°
      const steps1 = Math.max(1, Math.round(n * (360 - start) / span));
      for (let i = 0; i <= steps1; i++) {
        const b = start + i * (360 - start) / steps1;
        const p = this._destPoint(lat, lon, rKm, b % 360);
        coords.push(p);
      }
      // 第二段: 0° → end
      const steps2 = Math.max(1, n - steps1);
      for (let i = 1; i <= steps2; i++) {
        const b = i * end / steps2; // 从 0° 到 end (end < 360)
        const p = this._destPoint(lat, lon, rKm, b);
        coords.push(p);
      }
    } else {
      // 正常情况: 单段圆弧
      const steps = Math.max(2, n);
      for (let i = 0; i <= steps; i++) {
        const b = start + i * span / steps;
        const p = this._destPoint(lat, lon, rKm, b);
        coords.push(p);
      }
    }

    return coords;
  },

  // ── 扇区交叠面积计算 ─────────────────────────────────────────────
  /**
   * 计算两个扇区的交叠面积(平方米, WGS84近似)
   * 用于邻区交叠权重打分
   *
   * @param {Object} cell1  小区1 {lat, lon, azimuth, beamwidth, radius}
   * @param {Object} cell2  小区2
   * @returns {Number}       交叠面积(平方米)
   */
  computeSectorOverlap(cell1, cell2) {
    // 前端使用简化球面近似: 将扇区视为由采样射线组成的三角形集合,
    // 交叠 = 两个多边形 intersection 的近似面积
    // 为避免引入 heavy 依赖, 这里使用射线法简化
    // 若需要精确值, 应在 Python 后端用 Shapely 计算后返回
    // 此处返回 0, 实际打分依赖后端 geo_utils.sector_overlap_area
    return 0;
  },

  // ── 冲突高亮 ──────────────────────────────────────────────────
  highlightConflicts(conflictEcgis) {
    this.layers.conflicts.clearLayers();
    const ecgis = conflictEcgis instanceof Set ? conflictEcgis : new Set(conflictEcgis || []);
    ecgis.forEach(ecgi => {
      const c = this.cellIndex.get(ecgi);
      if (!c) return;
      const ring = L.circleMarker([c.lat, c.lon], {
        radius:    14,
        color:     this.CONFLICT_COLOR,
        fillColor: 'transparent',
        weight:    3,
        opacity:   0.8,
        className: 'conflict-pulse',
      }).addTo(this.layers.conflicts);
      ring.bindTooltip(
        `<b style="color:#dc2626">⚠ 冲突小区</b><br>${c.name || ecgi}`,
        { direction: 'top', offset: [0, -8] }
      );
    });
  },

  // ── 邻区连线 ──────────────────────────────────────────────────
  renderNeighborLines(srcEcgi) {
    this.layers.neighbors.clearLayers();
    const src = this.cellIndex.get(srcEcgi);
    if (!src) return;

    (src.neighbors || []).forEach(n => {
      const dst = this.cellIndex.get(n.dst_ecgi);
      if (!dst) return;

      // 扇区顶点对扇区顶点: src 扇区上指向 dst 的点 ↔ dst 扇区上指向 src 的点
      // 偏移半径 = cell.radius (扇区视觉半径, 来自 CASE 映射)
      const [srcLat, srcLon] = this._sectorPointTowards(src, dst.lat, dst.lon);
      const [dstLat, dstLon] = this._sectorPointTowards(dst, src.lat, src.lon);
      if (!isFinite(srcLat) || !isFinite(srcLon) || !isFinite(dstLat) || !isFinite(dstLon)) return;

      const path = L.polyline(
        [[srcLat, srcLon], [dstLat, dstLon]],
        { color: this.NEIGHBOR_COLOR, weight: 1.5, opacity: 0.7, dashArray: '4, 4' }
      ).addTo(this.layers.neighbors);
      path.bindTooltip(
        `${n.dst_name || n.dst_ecgi}<br>得分: ${(n.score || 0).toFixed(2)} | 距离: ${(n.distance_m || 0).toFixed(0)}m`,
        { sticky: true }
      );
    });
  },

  // ── 邻区连线(按类型+得分过滤) ───────────────────────────────────
  // 过滤状态（由 UI 滑块控制）
  _nbrScoreMin: 0,
  _nbrScoreType: 'all',

  setNbrScoreFilter(minScore, type) {
    this._nbrScoreMin = minScore;
    this._nbrScoreType = type;
    this._applyNbrScoreFilter();
  },

  _applyNbrScoreFilter() {
    if (!this._lastNbrData) return;
    this.renderNeighborsByType(this._lastPlannedEcgis, this._lastNbrData, true);
  },

  _getNbrScoreRange(nbrByType) {
    let mn = Infinity, mx = -Infinity;
    Object.values(nbrByType).forEach(recs => {
      recs.forEach(n => {
        const s = n.score || 0;
        if (s < mn) mn = s;
        if (s > mx) mx = s;
      });
    });
    return { min: mn === Infinity ? 0 : mn, max: mx === -Infinity ? 0 : mx };
  },

  renderNeighborsByType(plannedEcgis, nbrByType, isFilterUpdate = false) {
    // 保存原始数据供过滤重绘
    if (!isFilterUpdate) {
      this._lastNbrData = nbrByType;
      this._lastPlannedEcgis = plannedEcgis;
    }

    if (this._nbrByTypeLayers) {
      Object.values(this._nbrByTypeLayers).forEach(l => l.clearLayers && l.clearLayers());
    } else {
      this._nbrByTypeLayers = {};
    }
    this.layers.neighbors.clearLayers();

    // 几何参数(visualRadius/zoom)变化时清空所有 n._srcPt/n._dstPt 缓存
    // 端点 = 站点经纬度, 与任何几何参数都无关, 这里保留版本检测仅作防御
    const curZ = this.map ? this.map.getZoom() : 15;
    this._lastNbrZoom = curZ;

    const plannedSet = plannedEcgis instanceof Set ? plannedEcgis : new Set(plannedEcgis || []);
    if (!nbrByType) nbrByType = {};

    // 更新得分范围显示
    if (!isFilterUpdate) {
      const range = this._getNbrScoreRange(nbrByType);
      const rangeEl = document.getElementById('nbr-score-range');
      if (rangeEl) {
        rangeEl.textContent = `${range.min.toFixed(2)} ~ ${range.max.toFixed(2)}`;
      }
      // 初始化滑块: max = 当前最大值, value = 当前最小值 (动态读取规划邻区的最小得分)
      const slider = document.getElementById('nbr-score-min');
      if (slider) {
        slider.max = Math.ceil(range.max * 100);
        // 单站规划/局部规划: 滑块起始 = 实际最小值, 自动过滤掉"被规划算法筛掉"的低分噪声
        // 全网规划: range.min 通常是 0 (含所有弱邻区), 此时保持 0 以便查看全部
        const initMin = range.min > 0.01 ? Math.floor(range.min * 100) : 0;
        slider.value = initMin;
        document.getElementById('nbr-score-min-label').textContent = `≥${(initMin / 100).toFixed(2)}`;
        this._nbrScoreMin = initMin;
      }
    }

    const scoreMin = this._nbrScoreMin / 100;
    const scoreType = this._nbrScoreType;
    const types = ['4G_4G', '4G_5G', '5G_4G', '5G_5G'];

    types.forEach(t => {
      if (!this._nbrByTypeLayers[t]) {
        // 连线 layer 后建后 addTo, 处于同一 pane 顶层 (后绘制覆盖先绘制)
        this._nbrByTypeLayers[t] = L.layerGroup().addTo(this.map);
      }
      this._nbrByTypeLayers[t].clearLayers();
    });

    types.forEach(t => {
      const records = nbrByType[t] || [];
      const color = this.NBR_TYPE_COLORS[t] || '#666';
      const subLayer = this._nbrByTypeLayers[t];

      records.forEach(n => {
        const score = n.score || 0;
        // 类型过滤 + 得分过滤
        if (scoreType !== 'all' && t !== scoreType) return;
        if (score < scoreMin) return;

        const src = this.cellIndex.get(n.src_ecgi);
        const dst = this.cellIndex.get(n.dst_ecgi);
        if (!src || !dst) {
          if (window.__NBR_MISS && window.__NBR_MISS < 3) {
            console.log('[NBR missing]', t, n.src_ecgi, '->', n.dst_ecgi, 'src?', !!src, 'dst?', !!dst, 'cellIndex size:', this.cellIndex.size);
            window.__NBR_MISS = (window.__NBR_MISS || 0) + 1;
          }
          return;
        }

        // 缓存端点: 仅首次计算, 后续过滤/得分变更时直接复用,
        // 避免拖动滑块时端点重算导致位置抖动
        // 端点 = 站点经纬度 + 方位角 + 扇区视觉半径(cell.radius) 的扇区顶点
        // 即"扇区弧上指向对方的点", 几何语义稳定, 仅依赖 src/dst 自身的属性
        let [srcLat, srcLon] = n._srcPt || [null, null];
        let [dstLat, dstLon] = n._dstPt || [null, null];
        if (srcLat == null || dstLat == null) {
          [srcLat, srcLon] = this._sectorPointTowards(src, dst.lat, dst.lon);
          [dstLat, dstLon] = this._sectorPointTowards(dst, src.lat, src.lon);
          n._srcPt = [srcLat, srcLon];
          n._dstPt = [dstLat, dstLon];
        }

        const path = L.polyline(
          [[srcLat, srcLon], [dstLat, dstLon]],
          { color, weight: 3, opacity: 1.0, dashArray: '5, 4', pane: 'overlayPane' }
        ).addTo(subLayer);
        path.bindTooltip(
          `<b style="color:${color}">${t}</b><br>` +
          `${n.src_name || n.src_ecgi} (PCI ${n.src_pci ?? '—'})<br>` +
          `→ ${n.dst_name || n.dst_ecgi}<br>` +
          `距离: ${(n.distance_m || 0).toFixed(0)}m | 得分: ${score.toFixed(2)}` +
          `${n.same_freq ? ' | 同频' : ''}${n.cross_system ? ' | 异系统' : ''}`,
          { sticky: true, className: 'nbr-tooltip' }
        );
      });
    });

    this._rebuildNbrTypeControl();
  },

  _rebuildNbrTypeControl() {
    if (this._nbrTypeControl) {
      this.map.removeControl(this._nbrTypeControl);
      this._nbrTypeControl = null;
    }
    const overlays = {};
    Object.keys(this._nbrByTypeLayers || {}).forEach(t => {
      if (this._nbrByTypeLayers[t]) {
        overlays[`邻区-${t}`] = this._nbrByTypeLayers[t];
      }
    });
    if (Object.keys(overlays).length === 0) return;
    this._nbrTypeControl = L.control.layers(null, overlays, {
      position: 'topright',
      collapsed: false,
    }).addTo(this.map);
  },

  // ── 干扰渲染 ──────────────────────────────────────────────────
  renderInterference(issues) {
    this.layers.interference.clearLayers();
    if (!issues) return;
    // 大量连线会导致 Leaflet 卡顿, 仅渲染最关键的 Critical/High, 其它只标红 cell
    const MAX_LINES = 3000;
    let drawn = 0;
    for (const it of issues) {
      const s1 = it.sector1, s2 = it.sector2;
      if (!s1 || !s2) continue;
      if (drawn >= MAX_LINES && it.severity !== 'Critical') continue;
      const color = this.INTERFERENCE_COLORS[it.type] || '#666';
      const weight = it.severity === 'Critical' ? 3
                   : it.severity === 'High' ? 2.5 : 2;

      const line = L.polyline(
        [[s1.lat, s1.lon], [s2.lat, s2.lon]],
        { color, weight, opacity: 0.85 }
      ).addTo(this.layers.interference);
      line.bindTooltip(
        `<b>${it.type}</b> (${it.severity})<br>` +
        `${s1.name || s1.ecgi} (PCI ${s1.pci}) ↔ ${s2.name || s2.ecgi} (PCI ${s2.pci})<br>` +
        `距离: ${(it.distance_km || 0).toFixed(2)}km | ` +
        `Overlap: ${it.overlap1}% / ${it.overlap2}%<br>` +
        `${it.details || ''}`,
        { sticky: true }
      );
      drawn++;
    }
  },

  // ── 视图 ────────────────────────────────────────────────────
  fitBounds(cells) {
    if (!cells || cells.length === 0) return;
    const lats = cells.map(c => c.lat).filter(isFinite);
    const lons = cells.map(c => c.lon).filter(isFinite);
    if (lats.length === 0) return;
    const bounds = L.latLngBounds(
      [Math.min(...lats), Math.min(...lons)],
      [Math.max(...lats), Math.max(...lons)]
    );
    this.map.fitBounds(bounds, { padding: [40, 40] });
  },

  // ── 清除聚焦 ──────────────────────────────────────────────────
  clearFocus() {
    this._focusMode    = false;
    this._focusCenter  = null;
    this._focusRadiusKm = 0;
    if (this._nbrByTypeLayers) {
      Object.values(this._nbrByTypeLayers).forEach(l => l.clearLayers && l.clearLayers());
    }
    if (this._nbrTypeControl) {
      this.map.removeControl(this._nbrTypeControl);
      this._nbrTypeControl = null;
    }
    this.layers.interference.clearLayers();
    this._buildSectorLayerControl();
  },

  // ── 清除所有图层内容 ─────────────────────────────────────────────
  _clearAllLayers() {
    Object.values(this.layers).forEach(l => l && l.clearLayers());
  },

  clear() {
    this._clearAllLayers();
    this.cellIndex.clear();
    this.sectorCache.clear();
    this.conflictCache.clear();
  },

  /**
   * 更新视觉半径并重新渲染扇区
   * @param {Number} m 视觉半径(米)
   */
  setVisualRadius(m) {
    this.visualRadius = m;
    // 用缓存的状态重新渲染（自动应用新视觉半径）
    if (this._lastCells && this._lastCells.length > 0) {
      this.renderCells(this._lastCells, this._lastConflictEcgis);
    }
  },

  // ── 辅助 ─────────────────────────────────────────────────────
  _calcZoomForCenter(center, radiusKm, pad = 0.003) {
    const latOff = radiusKm / 111.0;
    const lonOff = radiusKm / (111.0 * Math.cos(center.lat * Math.PI / 180));
    const bounds = L.latLngBounds(
      [center.lat - latOff - pad, center.lon - lonOff - pad],
      [center.lat + latOff + pad, center.lon + lonOff + pad]
    );
    return Math.max(this.map.getBoundsZoom(bounds), 12);
  },

  _haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
              Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  },

  /**
   * 根据距离和方位角计算终点经纬度 (Haversine 前向问题)
   * @param {Number} lat      起点纬度
   * @param {Number} lon      起点经度
   * @param {Number} dist     距离(度)
   * @param {Number} bearing  方位角(正北0°, 顺时针)
   * @returns {Array}         [lat, lon]
   */
  _destPoint(lat, lon, distKm, bearing) {
    // distKm: 距离，单位公里（不是度数）
    const R = 6371; // km
    const br = bearing * Math.PI / 180;
    const latR = lat * Math.PI / 180;
    const lonR = lon * Math.PI / 180;
    const ad = distKm / R;
    const lat2 = Math.asin(
      Math.sin(latR) * Math.cos(ad) + Math.cos(latR) * Math.sin(ad) * Math.cos(br)
    );
    const lon2 = lonR + Math.atan2(
      Math.sin(br) * Math.sin(ad) * Math.cos(latR),
      Math.cos(ad) - Math.sin(latR) * Math.sin(lat2)
    );
    return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
  },

  // 两点间方位角（正北 0°，顺时针 0-360）
  _bearing(lat1, lon1, lat2, lon2) {
    const φ1 = lat1 * Math.PI / 180;
    const φ2 = lat2 * Math.PI / 180;
    const Δλ = (lon2 - lon1) * Math.PI / 180;
    const y = Math.sin(Δλ) * Math.cos(φ2);
    const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
    const θ = Math.atan2(y, x);
    return (θ * 180 / Math.PI + 360) % 360;
  },

    // (deprecated) 旧的 zoom-scaled 视觉半径方法, 不再使用 - 见 _renderCellSector 直接使用 cell.radius

  /**
   * 计算 cell 扇区弧上"指向 target"方向的顶点
   *  - 若 target 在扇区角度范围内 -> 取扇区中心→target 方位角上、半径处的顶点
   *  - 若 target 不在扇区角度范围内 -> 取扇区最靠近 target 方向的边界顶点
   * 偏移半径 = cell.radius (扇区视觉半径, CASE 映射), 与 zoom/UI 滑块无关
   * 用于邻区连线端点: src 顶点 ↔ dst 顶点
   */
  _sectorPointTowards(cell, targetLat, targetLon) {
    const lat = parseFloat(cell.lat);
    const lon = parseFloat(cell.lon);
    const az = parseFloat(cell.azimuth) || 0;
    const bw = parseFloat(cell.beamwidth || cell.beam) || 65;
    const caseR = parseFloat(cell.radius);
    // 必须与 renderCell 绘制公式一致 (rBase * VISUAL_SCALE), 否则端点会落在扇区"中间"而非"最高点"
    const rBase = (isFinite(caseR) && caseR > 0)
      ? Math.min(caseR, this.CASE_VISUAL_MAX_M)
      : 40;
    const rM = rBase * this.VISUAL_SCALE;

    const brg = this._bearing(lat, lon, targetLat, targetLon);
    const azN = ((az % 360) + 360) % 360;
    const halfBw = bw / 2;
    const startAz = ((azN - halfBw) % 360 + 360) % 360;
    const endAz   = ((azN + halfBw) % 360 + 360) % 360;

    // 判断 brg 是否落在 [startAz, endAz]（处理跨 0° 的扇区）
    const inSector = (startAz <= endAz)
      ? (brg >= startAz && brg <= endAz)
      : (brg >= startAz || brg <= endAz);

    let useBearing;
    if (inSector) {
      useBearing = brg;
    } else {
      const cw1  = ((brg - startAz) + 360) % 360;
      const ccw1 = ((startAz - brg) + 360) % 360;
      const cw2  = ((brg - endAz) + 360) % 360;
      const ccw2 = ((endAz - brg) + 360) % 360;
      const dStart = Math.min(cw1, ccw1);
      const dEnd   = Math.min(cw2, ccw2);
      useBearing = dStart <= dEnd ? startAz : endAz;
    }

    return this._destPoint(lat, lon, rM / 1000, useBearing);
  },

  // ── 区域圈选: 不引入 leaflet-draw, 自实现 mousedown→mousemove→mouseup ──
  // 调用方式: MapManager.enableDrawRect(callback) / enableDrawCircle(callback)
  // callback 收到 { type: 'rect'|'circle', ...rect/circle 字段 }

  enableDrawRect(cb) {
    this.disableDraw();
    this._drawMode = 'rect';
    this._areaCallback = cb;
    this._setCrosshair(true);
    this._bindDrawOnce();
  },

  enableDrawCircle(cb) {
    this.disableDraw();
    this._drawMode = 'circle';
    this._areaCallback = cb;
    this._setCrosshair(true);
    this._bindDrawOnce();
  },

  disableDraw() {
    if (this._drawEventsBound) {
      this.map.off('mousedown', this._drawEventsBound[0]);
      this.map.off('mousemove', this._drawEventsBound[1]);
      this.map.off('mouseup',   this._drawEventsBound[2]);
      this._drawEventsBound = null;
    }
    this._drawMode = null;
    this._drawStart = null;
    this._setCrosshair(false);
  },

  clearArea() {
    this._currentArea = null;
    if (this._drawShape) {
      this.layers.selection.removeLayer(this._drawShape);
      this._drawShape = null;
    }
  },

  getCurrentArea() {
    return this._currentArea;
  },

  setArea(area) {
    // 外部加载的 area 也能渲染 (例如重置或导入)
    this.clearArea();
    if (!area) return;
    if (area.type === 'rect') {
      const rect = L.rectangle(
        [
          [Math.min(area.lat1, area.lat2), Math.min(area.lon1, area.lon2)],
          [Math.max(area.lat1, area.lat2), Math.max(area.lon1, area.lon2)],
        ],
        { color: '#06b6d4', weight: 2, fillColor: '#06b6d4', fillOpacity: 0.06 }
      );
      this._drawShape = rect;
      this.layers.selection.addLayer(rect);
    } else if (area.type === 'circle') {
      const c = L.circle([area.lat, area.lon], {
        radius: area.radius_km * 1000,
        color: '#06b6d4', weight: 2, fillColor: '#06b6d4', fillOpacity: 0.06,
      });
      this._drawShape = c;
      this.layers.selection.addLayer(c);
    }
    this._currentArea = area;
  },

  _setCrosshair(on) {
    const c = this.map.getContainer();
    c.style.cursor = on ? 'crosshair' : '';
  },

  _bindDrawOnce() {
    const onDown = (e) => {
      if (this._drawMode !== 'rect' && this._drawMode !== 'circle') return;
      L.DomEvent.stopPropagation(e.originalEvent);
      L.DomEvent.preventDefault(e.originalEvent);
      this._drawStart = e.latlng;
      // 清除旧的临时/已确认图形
      this.clearArea();
      this._suppressContext = true;
    };
    const onMove = (e) => {
      if (!this._drawStart || !this._drawMode) return;
      const end = e.latlng;
      if (this._drawShape) {
        this.layers.selection.removeLayer(this._drawShape);
        this._drawShape = null;
      }
      if (this._drawMode === 'rect') {
        this._drawShape = L.rectangle(
          [this._drawStart, end],
          { color: '#06b6d4', weight: 2, dashArray: '4 4', fillColor: '#06b6d4', fillOpacity: 0.06 }
        );
      } else {
        const rM = this._drawStart.distanceTo(end);
        this._drawShape = L.circle(this._drawStart, {
          radius: rM,
          color: '#06b6d4', weight: 2, dashArray: '4 4', fillColor: '#06b6d4', fillOpacity: 0.06,
        });
      }
      this._drawShape.addTo(this.layers.selection);
    };
    const onUp = (e) => {
      if (!this._drawStart || !this._drawMode) return;
      const end = e.latlng;
      const start = this._drawStart;
      this._drawStart = null;
      this._suppressContext = false;
      // 移除临时图层, 重新画实线
      if (this._drawShape) {
        this.layers.selection.removeLayer(this._drawShape);
        this._drawShape = null;
      }
      // 太小的拖拽忽略
      if (start.distanceTo(end) < 50) return;
      let area;
      if (this._drawMode === 'rect') {
        area = {
          type: 'rect',
          lat1: start.lat, lon1: start.lng,
          lat2: end.lat, lon2: end.lng,
        };
      } else {
        area = {
          type: 'circle',
          lat: start.lat, lon: start.lng,
          radius_km: start.distanceTo(end) / 1000,
        };
      }
      // 渲染实线最终区域
      this.setArea(area);
      this.disableDraw();
      if (this._areaCallback) {
        const cb = this._areaCallback;
        this._areaCallback = null;
        cb(area);
      }
    };

    this.map.on('mousedown', onDown);
    this.map.on('mousemove', onMove);
    this.map.on('mouseup', onUp);
    this._drawEventsBound = [onDown, onMove, onUp];
  },
};

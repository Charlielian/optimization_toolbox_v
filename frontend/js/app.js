// 主控制器
const App = {
  cells: [],
  conflicts: [],
  stats: {},
  pciQualityByEcgi: {},

  _qualityLabelColor(label) {
    const m = { 优: '#51cf66', 良: '#4dabf7', 一般: '#ffa94d', 偏差: '#ff922b', 需关注: '#ff6b6b' };
    return m[label] || '#9aa0b0';
  },

  _mergePciQualityReport(report) {
    if (!report?.cells?.length) return;
    this.pciQualityByEcgi = {};
    report.cells.forEach((q) => {
      if (q.ecgi) this.pciQualityByEcgi[q.ecgi] = q;
    });
    this.cells.forEach((c) => {
      const full = this.pciQualityByEcgi[c.ecgi];
      if (full) c.pci_quality = full;
    });
  },

  _renderPciQualityBlock(q, { compact = false } = {}) {
    if (!q) return '';
    const col = this._qualityLabelColor(q.quality_label);
    const scoreLine = `<div class="row"><div class="key">冲突评分</div><div class="val" style="color:${col};font-weight:600;">${q.score} · ${q.quality_label || '—'}</div></div>`;
    const explain = q.score_explain
      ? `<div style="font-size:10px;color:#888;line-height:1.45;margin:4px 0 8px;">${q.score_explain}</div>`
      : '';
    const nearest = (key, title) => {
      const n = q[key];
      if (!n) return `<div class="row"><div class="key">${title}</div><div class="val" style="color:#666;">范围内无</div></div>`;
      return `<div class="row"><div class="key">${title}</div><div class="val">${n.name || n.ecgi} · PCI ${n.pci} · ${n.distance_km}km · ${n.relation}</div></div>`;
    };
    let interHtml = '';
    const tops = q.top_interference || [];
    if (tops.length) {
      interHtml = `<h4 style="color:#ccc;margin:8px 0 4px;font-size:11px;">主要干扰来源 (${tops.length})</h4>
        <div class="neighbor-list" style="max-height:${compact ? 120 : 200}px;">
          ${tops.map((t) => `
            <div class="neighbor-item">
              <div class="name">${t.name || t.ecgi} <span style="color:#888;">PCI ${t.pci}</span></div>
              <div class="meta">${t.distance_km}km · ${t.relation} · 贡献 ${t.penalty}${t.back_facing ? ' · 背向减半' : ''}</div>
            </div>`).join('')}
        </div>`;
    } else if (!compact) {
      interHtml = '<div style="font-size:11px;color:#666;margin-top:6px;">评估半径内无显著 PCI 层干扰贡献</div>';
    }
    let candHtml = '';
    const cands = q.pci_candidates_scores || [];
    if (cands.length && !compact) {
      candHtml = `<div style="margin-top:8px;font-size:10px;color:#888;">候选 PCI 评分: ${cands.map((x) => `${x.pci}(${x.score})${x.is_chosen ? '✓' : ''}`).join(' · ')}</div>`;
    }
    let hard = '';
    if (q.hard_violations?.length) {
      hard = `<div style="margin-top:6px;padding:6px;background:rgba(255,107,107,0.1);border-radius:4px;font-size:10px;color:#ff6b6b;">硬约束: ${q.hard_violations.slice(0, 3).join('; ')}</div>`;
    }
    return `${scoreLine}${explain}${nearest('nearest_cell', '最近小区')}${nearest('nearest_same_pci', '最近同PCI')}${nearest('nearest_mod3', '最近Mod3')}${hard}${interHtml}${candHtml}`;
  },

  _renderPciQualitySummary(summary) {
    if (!summary || !summary.cells_reported) return '';
    const dist = summary.quality_distribution || {};
    const parts = ['优', '良', '一般', '偏差', '需关注'].filter((k) => dist[k]).map((k) => `${k}:${dist[k]}`);
    return `
      <div class="info-card" style="border-left:3px solid #4dabf7;margin-bottom:8px;">
        <div style="font-size:12px;color:#4dabf7;font-weight:bold;margin-bottom:4px;">PCI 质量概览</div>
        <div class="row"><div class="key">已评估</div><div class="val">${summary.cells_reported} 扇区${summary.truncated ? ' (已截断)' : ''}</div></div>
        <div class="row"><div class="key">平均评分</div><div class="val">${summary.avg_score}（越小越好）</div></div>
        <div class="row"><div class="key">分布</div><div class="val" style="font-size:11px;">${parts.join(' · ') || '—'}</div></div>
      </div>`;
  },

  init() {
    MapManager.init('map');
    this.bindEvents();
    this.log('系统就绪, 请上传工参或加载示例数据', 'info');
    // 检查后端
    this.healthCheck();
    // 自动从数据库恢复上次数据
    this.refreshCells();
  },

  async healthCheck() {
    try {
      const r = await fetch('/api/health');
      const j = await r.json();
      this.log(`后端已连接, 版本 ${j.version}`, 'success');
    } catch (e) {
      this.log('后端连接失败, 请确认服务已启动', 'error');
    }
  },

  bindEvents() {
    this.restoreLogs();
    const $ = id => document.getElementById(id);

    // ── 数据导入相关 (可能不存在, 已移到独立页面)
    if ($('btn-upload') && $('file-input')) {
      $('btn-upload').onclick = () => $('file-input').click();
      $('file-input').onchange = (e) => this.handleUpload(e.target.files[0]);
    }
    if ($('btn-sample')) $('btn-sample').onclick = () => this.loadSample();
    if ($('btn-template-4g')) $('btn-template-4g').onclick = () => API.downloadTemplate('4G').catch(e => this._logErr('模板下载', e, { rat: '4G' }));
    if ($('btn-template-5g')) $('btn-template-5g').onclick = () => API.downloadTemplate('5G').catch(e => this._logErr('模板下载', e, { rat: '5G' }));
    if ($('btn-template-both')) $('btn-template-both').onclick = () => API.downloadTemplate('both').catch(e => this._logErr('模板下载', e, { rat: 'both' }));
    if ($('btn-clear-db')) $('btn-clear-db').onclick = () => this.clearDatabase();

    // ── 规划相关
    if ($('btn-plan-all')) $('btn-plan-all').onclick = () => this.planAll();
    if ($('btn-plan-partial')) $('btn-plan-partial').onclick = () => this.planPartial();
    this._refreshPlanScope();
    if ($('btn-check-conflict')) $('btn-check-conflict').onclick = () => this.checkConflict();
    if ($('btn-check-redundancy')) $('btn-check-redundancy').onclick = () => this.checkRedundancy();
    if ($('btn-export-workparams')) $('btn-export-workparams').onclick = () => this.exportFile('workparams');
    if ($('btn-export-neighbors')) $('btn-export-neighbors').onclick = () => this.exportFile('neighbors');
    if ($('btn-export-conflicts')) $('btn-export-conflicts').onclick = () => this.exportFile('conflicts');
    if ($('btn-export-summary')) $('btn-export-summary').onclick = () => this.exportFile('summary');
    if ($('btn-export-mml')) $('btn-export-mml').onclick = () => this.exportMML();

    // ── 扇区显示控制：按钮切换视觉半径
    document.querySelectorAll('[data-radius]').forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll('[data-radius]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const m = parseInt(btn.dataset.radius, 10);
        MapManager.setVisualRadius(m);
      };
    });
    if ($('btn-reset-view')) $('btn-reset-view').onclick = () => {
      if (this.cells.length) MapManager.fitBounds(this.cells);
    };
    if ($('btn-zoom-4g')) $('btn-zoom-4g').onclick = () => this._zoomToRat('LTE');
    if ($('btn-zoom-5g')) $('btn-zoom-5g').onclick = () => this._zoomToRat('NR');

    // ── 单站/批量规划 (新)
    if ($('btn-plan-single')) $('btn-plan-single').onclick = () => this.planSingle();
    if ($('btn-export-split')) $('btn-export-split').onclick = () => this.exportSplit();
    if ($('btn-batch-tpl-4g')) $('btn-batch-tpl-4g').onclick = () => API.downloadTemplate('4G').catch(e => this._logErr('模板下载', e, { rat: '4G' }));
    if ($('btn-batch-tpl-5g')) $('btn-batch-tpl-5g').onclick = () => API.downloadTemplate('5G').catch(e => this._logErr('模板下载', e, { rat: '5G' }));
    if ($('btn-batch-tpl-both')) $('btn-batch-tpl-both').onclick = () => API.downloadTemplate('both').catch(e => this._logErr('模板下载', e, { rat: 'both' }));
    if ($('btn-batch-pick')) $('btn-batch-pick').onclick = () => {
      const f = $('batch-file');
      if (f) f.click();
    };
    const batchInput = $('batch-file');
    if (batchInput) {
      batchInput.onchange = (e) => {
        const f = e.target.files[0];
        if ($('batch-filename')) $('batch-filename').textContent = f ? f.name : '';
      };
    }
    if ($('btn-batch-plan')) $('btn-batch-plan').onclick = () => this.planBatchAndExport();

    // ── 单站规划: 频段随制式切换
    this._initSingleSiteFreqSelector();

    // ── 日志面板: 复制 / 清空
    document.getElementById('btn-log-copy')?.addEventListener('click', () => this.copyLogs());
    document.getElementById('btn-log-clear')?.addEventListener('click', () => this.clearLogs());

    // ── 干扰分析 (新)
    if ($('btn-ia-analyze')) $('btn-ia-analyze').onclick = () => this.analyzeInterference();
    if ($('btn-ia-export')) $('btn-ia-export').onclick = () => this.exportInterference();
    // 地图圈选（干扰分析 / PCI 局部微调共用同一选区）
    const onAreaSelected = (area, logPrefix) => {
      this._updateAreaInfo(area);
      this._updatePciAreaInfo(area);
      this._refreshPlanScope();
      if (area.type === 'rect') {
        this.log(`${logPrefix} 已框选矩形: ${area.lat1.toFixed(4)},${area.lon1.toFixed(4)} → ${area.lat2.toFixed(4)},${area.lon2.toFixed(4)}`, 'success');
      } else if (area.type === 'circle') {
        this.log(`${logPrefix} 已圈选圆形: 中心 ${area.lat.toFixed(4)},${area.lon.toFixed(4)} 半径 ${area.radius_km.toFixed(2)}km`, 'success');
      } else if (area.type === 'polygon') {
        this.log(`${logPrefix} 已圈选多边形: ${(area.points || []).length} 个顶点`, 'success');
      }
    };
    const clearMapArea = (logPrefix) => {
      MapManager.disableDraw();
      MapManager.clearArea();
      const iaInfo = $('ia-area-info');
      if (iaInfo) iaInfo.innerText = '已清除圈选(干扰分析将作用于全网)';
      const pciInfo = $('pci-area-info');
      if (pciInfo) pciInfo.innerText = '已清除圈选(局部微调将作用于地图可视范围)';
      this._refreshPlanScope();
      this.log(`${logPrefix} 已清除选区`, 'info');
    };
    if ($('btn-ia-draw-rect')) $('btn-ia-draw-rect').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawRect((area) => onAreaSelected(area, '[干扰分析]'));
      this.log('[干扰分析] 在地图上拖拽画出矩形选区', 'info');
    };
    if ($('btn-ia-draw-circle')) $('btn-ia-draw-circle').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawCircle((area) => onAreaSelected(area, '[干扰分析]'));
      this.log('[干扰分析] 在地图上拖拽画出圆形选区', 'info');
    };
    if ($('btn-ia-draw-polygon')) $('btn-ia-draw-polygon').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawPolygon((area) => onAreaSelected(area, '[干扰分析]'));
      this.log('[干扰分析] 多边形：单击加点，双击结束，Esc 取消', 'info');
    };
    if ($('btn-ia-clear-area')) $('btn-ia-clear-area').onclick = () => clearMapArea('[干扰分析]');
    if ($('btn-pci-draw-rect')) $('btn-pci-draw-rect').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawRect((area) => onAreaSelected(area, '[PCI规划]'));
      this.log('[PCI规划] 在地图上拖拽画出矩形 → 再点「局部微调」', 'info');
    };
    if ($('btn-pci-draw-circle')) $('btn-pci-draw-circle').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawCircle((area) => onAreaSelected(area, '[PCI规划]'));
      this.log('[PCI规划] 在地图上拖拽画出圆形 → 再点「局部微调」', 'info');
    };
    if ($('btn-pci-draw-polygon')) $('btn-pci-draw-polygon').onclick = () => {
      MapManager.disableDraw();
      MapManager.clearArea();
      MapManager.enableDrawPolygon((area) => onAreaSelected(area, '[PCI规划]'));
      this.log('[PCI规划] 多边形：单击加点，双击结束 → 再点「局部微调」', 'info');
    };
    if ($('btn-pci-clear-area')) $('btn-pci-clear-area').onclick = () => clearMapArea('[PCI规划]');
    // 干扰分析: 网络制式切换时, 刷新频段下拉
    if ($('ia-rat')) {
      $('ia-rat').onchange = () => this._refreshIaFreqbandOptions();
      this._refreshIaFreqbandOptions();
      this._refreshPciFreqbandOptions();
    }
    if ($('pci-rat')) {
      $('pci-rat').onchange = () => {
        this._refreshPciFreqbandOptions();
        this._refreshPlanScope();
        const area = MapManager.getCurrentArea?.();
        if (area) this._updatePciAreaInfo(area);
      };
      this._refreshPciFreqbandOptions();
    }
    if ($('pci-freqband')) {
      $('pci-freqband').onchange = () => {
        this._refreshPlanScope();
        const area = MapManager.getCurrentArea?.();
        if (area) this._updatePciAreaInfo(area);
      };
    }

    // ── 邻区得分过滤
    if ($('nbr-score-min')) {
      $('nbr-score-min').addEventListener('input', (e) => {
        const v = parseInt(e.target.value, 10);
        if ($('nbr-score-min-label')) $('nbr-score-min-label').textContent = `≥${(v / 100).toFixed(2)}`;
        MapManager.setNbrScoreFilter(v, MapManager._nbrScoreType);
      });
    }
    if ($('nbr-score-type')) {
      $('nbr-score-type').addEventListener('change', (e) => {
        MapManager.setNbrScoreFilter(MapManager._nbrScoreMin, e.target.value);
      });
    }
  },

  // ──────────────────────────────────────────────
  // 单站规划
  // ──────────────────────────────────────────────

  /**
   * 单站规划: 频段下拉框随制式 (4G/5G) 切换
   * - 5G: 700M / 2.6G / 4.9G
   * - 4G: FDD900 / FDD1800 / F / D / A / E
   * "默认" 在两种制式下都可用
   */
  _initSingleSiteFreqSelector() {
    const FREQ_4G = [
      { v: 'FDD900',  t: 'FDD900' },
      { v: 'FDD1800', t: 'FDD1800' },
      { v: 'F',       t: 'F (1885MHz)' },
      { v: 'D',       t: 'D (2575MHz)' },
      { v: 'A',       t: 'A (2010MHz)' },
      { v: 'E',       t: 'E (2325MHz)' },
      { v: '默认',    t: '默认' },
    ];
    const FREQ_5G = [
      { v: '700M',    t: '700M' },
      { v: '2.6G',    t: '2.6G' },
      { v: '4.9G',    t: '4.9G' },
      { v: '默认',    t: '默认' },
    ];

    const ratSel = document.getElementById('ss-rat');
    const freqSel = document.getElementById('ss-freq');
    if (!ratSel || !freqSel) return;

    const rebuild = () => {
      const rat = ratSel.value;
      const list = (rat === '4G') ? FREQ_4G : FREQ_5G;
      const prevValue = freqSel.value;
      freqSel.innerHTML = '';
      list.forEach(({ v, t }) => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = t;
        freqSel.appendChild(opt);
      });
      // 尝试保留原值, 不可用时回落到第一项
      if (list.some(x => x.v === prevValue)) {
        freqSel.value = prevValue;
      } else {
        freqSel.selectedIndex = 0;
      }
    };

    ratSel.addEventListener('change', rebuild);
    rebuild();

    // ── 单站规划: 邻区类型随制式联动 ──
    // 规则:
    //   规划 5G 小区 → 仅允许 5G↔5G, 5G↔4G (禁止 4G↔4G, 4G↔5G)
    //   规划 4G 小区 → 仅允许 4G↔4G, 4G↔5G (禁止 5G↔5G, 5G↔4G)
    // 实现: 制式切换时禁用不允许的复选框 + 自动取消勾选; 加载时同步一次初始状态
    const NBR_RAT_RULES = {
      '5G': { enable: ['ss-nbr-5g5g', 'ss-nbr-5g4g'], disable: ['ss-nbr-4g4g', 'ss-nbr-4g5g'] },
      '4G': { enable: ['ss-nbr-4g4g', 'ss-nbr-4g5g'], disable: ['ss-nbr-5g5g', 'ss-nbr-5g4g'] },
    };
    const applyNbrRule = (rat) => {
      const rule = NBR_RAT_RULES[rat];
      if (!rule) return;
      rule.enable.forEach(id => {
        const cb = document.getElementById(id);
        if (cb) { cb.disabled = false; cb.parentElement.style.opacity = '1'; }
      });
      rule.disable.forEach(id => {
        const cb = document.getElementById(id);
        if (cb) {
          cb.checked = false;
          cb.disabled = true;
          cb.parentElement.style.opacity = '0.4';
        }
      });
    };
    ratSel.addEventListener('change', () => applyNbrRule(ratSel.value));
    applyNbrRule(ratSel.value);

    // 扇区数/方位角联动提示
    const nsEl = document.getElementById('ss-nsectors');
    const azEl = document.getElementById('ss-az');
    const hintEl = document.getElementById('ss-az-hint');
    const _updateHint = () => {
      const n = parseInt(nsEl.value, 10) || 1;
      if (n === 1) {
        hintEl.textContent = '单扇区: 0-360';
        azEl.placeholder = '0-360';
      } else {
        hintEl.textContent = `${n}扇区: 逗号分隔`;
        azEl.placeholder = `例: 30,${30 + 360 / n},${30 + 2 * 360 / n}…`;
      }
    };
    nsEl.addEventListener('input', _updateHint);
    _updateHint();
  },

  async planSingle() {
    const lon = parseFloat(document.getElementById('ss-lon').value);
    const lat = parseFloat(document.getElementById('ss-lat').value);
    if (!isFinite(lon) || !isFinite(lat)) {
      this.log('经纬度无效', 'error');
      return;
    }
    const rat = document.getElementById('ss-rat').value;
    const freq = document.getElementById('ss-freq').value;
    const ptype = document.getElementById('ss-ptype').value;
    const nSectors = parseInt(document.getElementById('ss-nsectors').value, 10);
    if (!Number.isInteger(nSectors) || nSectors < 1 || nSectors > 6) {
      this.log('扇区数必须是 1-6 的整数', 'error');
      return;
    }

    // 基方位角解析
    // 扇区数=1 → 单个数字 0-360
    // 扇区数>1 → 逗号分隔的方位角列表，数量必须 == 扇区数
    const azRaw = (document.getElementById('ss-az').value || '').trim();
    let baseAz;
    if (nSectors === 1) {
      const n = parseFloat(azRaw);
      if (!isFinite(n) || n < 0 || n > 360) {
        this.log('扇区数=1 时，基方位角必须是 0-360 之间的数字', 'error');
        return;
      }
      baseAz = n;
    } else {
      const parts = azRaw.split(',').map(s => parseFloat(s.trim())).filter(isFinite);
      if (parts.length !== nSectors) {
        this.log(`扇区数=${nSectors}，需要填入 ${nSectors} 个方位角（用逗号分隔），当前只填了 ${parts.length} 个`, 'error');
        return;
      }
      if (parts.some(v => v < 0 || v > 360)) {
        this.log('每个方位角值必须在 0-360 之间', 'error');
        return;
      }
      baseAz = parts;
    }
    const nbrTypes = [];
    if (document.getElementById('ss-nbr-4g4g').checked) nbrTypes.push('4G_4G');
    if (document.getElementById('ss-nbr-4g5g').checked) nbrTypes.push('4G_5G');
    if (document.getElementById('ss-nbr-5g4g').checked) nbrTypes.push('5G_4G');
    if (document.getElementById('ss-nbr-5g5g').checked) nbrTypes.push('5G_5G');
    if (nbrTypes.length === 0) {
      this.log('请至少勾选一种邻区规划类型', 'warn');
      return;
    }

    const engine = document.querySelector('input[name="pci-engine"]:checked')?.value || 'legacy';
    const checkMod6 = !!document.getElementById('check-mod6')?.checked;
    const checkMod30 = !!document.getElementById('check-mod30')?.checked;
    const reuseKm = parseFloat(document.getElementById('reuse-distance')?.value || 5.0);
    const useBeam = !!document.getElementById('use-beam-overlap')?.checked;
    const scoreThreshold = parseFloat(document.getElementById('ss-score-threshold')?.value || 0.5);
    const planningMode = document.getElementById('ss-plan-mode')?.value || 'pci+nbr';
    const directionalFilter = !!document.getElementById('directional-filter')?.checked;

    const payload = {
      lat, lon,
      rat, freq_band: freq,
      plan_site_type: ptype,
      n_sectors: nSectors,
      base_azimuth: baseAz,
      nbr_plan_types: nbrTypes,
      engine,
      reuse_distance_km: reuseKm,
      check_mod6: checkMod6,
      check_mod30: checkMod30,
      use_beam_overlap_score: useBeam,
      score_threshold: scoreThreshold,
      planning_mode: planningMode,
      directional_filter: directionalFilter,
    };

    this.log(`[单站规划] ${rat} ${freq} ${ptype} @${lat},${lon} 扇区数${nSectors} engine=${engine}`, 'info');
    this._setBtnLoading('btn-plan-single', true);
    this._progress.show('单站规划中…');
    this._progress.update(5, '正在上传参数…');
    try {
      const r = await API.planSingle(payload, (pct, stage) => {
        this._progress.update(pct, stage);
      });
      if (!r.success) throw new Error(r.detail || '规划失败');
      // 清理前端 this.cells 中所有旧的 PLAN-* 临时小区 (不论位置)
      // - 这些临时小区由 plan_single_site 生成, 后端不入库 (db.save_all 过滤 is_temp=True)
      // - 但前端 App.cells 会累积: 多次规划 / 切换位置规划, 旧 PLAN-* 一直留在 cells 里
      // - 若不清, focusedCells 在新中心点 radiusKm 内的旧 PLAN-* 会被当作邻居渲染,
      //   在地图上残留"规划站"集群, 视觉上与新规划站混淆
      const newPlannedSet = new Set(r.planned_ecgis);
      const beforeLen = this.cells.length;
      this.cells = this.cells.filter(c => !String(c.ecgi || '').startsWith('PLAN-'));
      const removedStale = beforeLen - this.cells.length;
      if (removedStale > 0) {
        this.log(`[单站规划] 清理 ${removedStale} 个旧规划小区 (PLAN-*)`, 'info');
      }
      this.cells = this.cells.concat(r.planned_cells);
      const plannedSet = newPlannedSet;
      // 单站规划: 进入聚焦模式(仅渲染中心 radiusKm 内的小区为扇区, 自动 fitView)
      // 这样规划小区与邻居都能正确绘制为扇区多边形, 而不是仅圆点
      const focusRadiusKm = Math.max(parseFloat(reuseKm) * 1.5, 3);
      const center = { lat: parseFloat(lat), lon: parseFloat(lon) };
      // keepView=true: 局部刷新 (清空扇区再渲染) 但不动地图视图, 让用户保持当前 zoom/pan
      MapManager.focusedCells(this.cells, center, focusRadiusKm, plannedSet, { keepView: true });
      // 渲染邻区连线
      const nbrCounts = Object.fromEntries(Object.entries(r.nbr_by_type).map(([k, v]) => [k, v.length]));
      console.log('[DEBUG] nbr_by_type counts:', nbrCounts, 'first record:', r.nbr_by_type['5G_5G']?.[0]);
      MapManager.renderNeighborsByType(plannedSet, r.nbr_by_type);
      this._lastPlannedEcgis = r.planned_ecgis;
      this._lastNbrTypes = nbrTypes;
      this._setStatusWithSectors();
      this.log(`[单站规划] 完成: ${r.planned_ecgis.length} 扇区, 邻区 ${JSON.stringify(r.nbr_counts)}`, 'success');
      this._progress.done(`完成 ${r.planned_ecgis.length} 扇区`);
      this.showPlannedDetail(r);
    } catch (e) {
      const msg = e?.message || String(e);
      this._progress.error(msg.slice(0, 60));
      if (msg.includes('504') || msg.includes('超时')) {
        this.log(`[单站规划] 超时: ${msg}，请尝试减小「复用距离」或减少「邻区规划类型」`, 'error', payload);
      } else {
        this._logErr('单站规划', e, payload);
      }
    } finally {
      this._setBtnLoading('btn-plan-single', false);
    }
  },

  showPlannedDetail(result) {
    // 1. 写日志（保留）
    const lines = [];
    lines.push(`规划小区 (${result.planned_cells.length} 个):`);
    result.planned_cells.forEach(c => {
      lines.push(`  - ${c.name} (${c.rat}) PCI ${c.pci} → ${c.new_pci}`);
    });
    lines.push('', '邻区按类型:');
    Object.entries(result.nbr_counts || {}).forEach(([t, n]) => {
      lines.push(`  ${t}: ${n}`);
    });
    this.log(lines.join('\n'), 'info');

    // 2. 填充右侧详情面板
    const detail = document.getElementById('detail-body');
    const center = result.center || {};
    const planTypeZh = { macro: '宏站', micro: '微站', indoor: '室分' };
    const ratZh = { LTE: '4G', NR: '5G' };

    // ── 按 site_name 分组: 同一站点 1 张卡, 扇区按 sector_index 排序 ──
    const bySite = new Map();
    result.planned_cells.forEach(c => {
      const key = c.site_name || c.name || c.ecgi;
      if (!bySite.has(key)) {
        bySite.set(key, {
          siteName: key,
          plan_site_type: c.plan_site_type,
          freq_band: c.freq_band,
          rat: c.rat,
          lat: c.lat,
          lon: c.lon,
          cells: [],
        });
      }
      bySite.get(key).cells.push(c);
    });
    // 扇区按 sector_index 1, 2, 3 顺序排
    bySite.forEach(g => g.cells.sort((a, b) => (a.sector_index || 0) - (b.sector_index || 0)));

    // 取首站信息给顶部"规划站点"卡
    const firstSite = bySite.values().next().value;
    const headCells = firstSite ? firstSite.cells : [];
    const headRat = ratZh[headCells[0]?.rat] || headCells[0]?.rat || '-';

    // 规划站点卡片 (一站一张, 不再重复 3 次)
    let html = `
      <div class="info-card" style="border-left: 3px solid #facc15;">
        <div style="font-size: 13px; color: #facc15; font-weight: bold; margin-bottom: 6px;">
          📍 规划站点: ${firstSite?.siteName || '-'}
        </div>
        <div class="row"><div class="key">经纬度</div><div class="val">${firstSite?.lon?.toFixed(6) || '?'}, ${firstSite?.lat?.toFixed(6) || '?'}</div></div>
        <div class="row"><div class="key">制式 / 频段</div><div class="val">${headRat} · ${firstSite?.freq_band || '-'}</div></div>
        <div class="row"><div class="key">类型</div><div class="val">${planTypeZh[firstSite?.plan_site_type] || firstSite?.plan_site_type || '-'}</div></div>
        <div class="row"><div class="key">扇区数</div><div class="val">${headCells.length}</div></div>
      </div>
      <h4 style="color: #ccc; margin: 8px 0 6px; font-size: 12px;">扇区规划 (${result.planned_cells.length} 扇区 / ${bySite.size} 站)</h4>
    `;

    if (result.pci_quality?.summary) {
      this._mergePciQualityReport(result.pci_quality);
      html += this._renderPciQualitySummary(result.pci_quality.summary);
    }

    // 一站一张卡: SSS 组一行 + 扇区 PCI 一行 + 候选 SSS 组一行
    bySite.forEach(g => {
      const typeColor = MapManager.PLAN_COLORS[g.plan_site_type] || '#facc15';
      const typeZh = planTypeZh[g.plan_site_type] || g.plan_site_type || '—';
      const ratCell = g.cells[0];
      const ratDisplay = ratZh[ratCell?.rat] || ratCell?.rat || '';

      // ── 候选 SSS 组: 1 个当前 + 4 个候选 = 共 5 行, 每行一组, 不换行 ──
      // 每行布局: [当前/候N] [SSS组号] [PCI chips 横向不换行, 溢出滚动]
      const pciGroups = ratCell?.pci_groups || [];
      const chosenPcis = new Set(g.cells.map(c => c.new_pci));
      let groupIdxCounter = 0;
      const groupRowsHtml = pciGroups.length > 0
        ? pciGroups.map((grp) => {
            const pcs = grp.pcis.map((pp) => {
              const isChosen = chosenPcis.has(pp);
              const isPrimary = pp === grp.pcis[0] && isChosen && grp.is_current;
              if (isPrimary) {
                return `<span title="主选 PCI ${pp}" style="display:inline-block;padding:0 4px;margin-right:1px;border-radius:2px;background:#67c23a;color:#fff;font-weight:bold;font-size:10px;line-height:1.4;">${pp}</span>`;
              }
              if (isChosen) {
                return `<span title="已选 PCI ${pp}" style="display:inline-block;padding:0 4px;margin-right:1px;border-radius:2px;background:#3a7a3a;color:#fff;font-size:10px;line-height:1.4;">${pp}</span>`;
              }
              const bg = grp.is_current ? 'rgba(250,204,21,0.10)' : 'rgba(167,139,250,0.10)';
              const col = grp.is_current ? '#facc15' : '#a78bfa';
              return `<span style="display:inline-block;padding:0 4px;margin-right:1px;border-radius:2px;background:${bg};color:${col};font-size:10px;line-height:1.4;">${pp}</span>`;
            }).join('');
            const tagColor = grp.is_current ? '#67c23a' : '#a78bfa';
            const tagBg = grp.is_current ? 'rgba(103,194,58,0.15)' : 'rgba(167,139,250,0.15)';
            const tagText = grp.is_current ? '当前' : `候${groupIdxCounter++}`;
            return `<div style="display:flex;align-items:center;gap:4px;margin-top:2px;line-height:1.4;">
              <span style="display:inline-block;background:${tagBg};color:${tagColor};font-size:9px;padding:0 4px;border-radius:2px;line-height:1.5;flex-shrink:0;min-width:24px;text-align:center;">${tagText}</span>
              <span style="font-size:9px;color:#888;flex-shrink:0;">SSS=${grp.sss_group}</span>
              <span style="display:inline-flex;align-items:center;flex-wrap:nowrap;overflow-x:auto;flex:1;min-width:0;">${pcs}</span>
            </div>`;
          }).join('')
        : '';

      // 扇区 PCI 行: 一行横排, 每个 PCI 后挂小号 Sx 上标 (354ˢ¹ 355ˢ² 356ˢ³)
      const pciRow = g.cells.map((c, idx) => {
        const sec = c.sector_index || (idx + 1);
        return `<span style="display:inline-block;white-space:nowrap;padding:0 2px;">${c.new_pci}<sup style="font-size:9px;color:#888;font-weight:normal;margin-left:1px;">S${sec}</sup></span>`;
      }).join('<span style="color:#555;margin:0 2px;font-size:13px;">/</span>');

      html += `
        <div class="info-card" style="border-left: 3px solid ${typeColor};">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span style="font-weight:bold;color:${typeColor};font-size:13px;">${g.siteName}</span>
            <span style="font-size:10px;color:#888;">${typeZh} · ${ratDisplay} · ${g.freq_band || ''}</span>
          </div>

          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;">
            <span style="font-size:10px;color:#888;white-space:nowrap;">新 PCI:</span>
            <span style="font-size:15px;font-weight:bold;color:#67c23a;letter-spacing:0.5px;">${pciRow}</span>
          </div>

          ${groupRowsHtml ? `
          <div style="margin-top:4px;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.06);">
            <div style="font-size:9px;color:#666;margin-bottom:2px;">候选 SSS 组 (${pciGroups.length} 组, 各 ${g.cells.length} PCI)</div>
            <div style="max-height:96px;overflow-y:auto;">${groupRowsHtml}</div>
          </div>
          ` : ''}

          <div style="font-size:9px;color:#666;margin-top:4px;">经 ${g.lon?.toFixed(6)}, ${g.lat?.toFixed(6)}</div>
          ${g.cells.map((c, idx) => {
            const q = c.pci_quality || this.pciQualityByEcgi[c.ecgi];
            if (!q) return '';
            const sec = c.sector_index || (idx + 1);
            return `<div style="margin-top:8px;padding-top:6px;border-top:1px dashed rgba(255,255,255,0.06);">
              <div style="font-size:10px;color:#888;margin-bottom:4px;">扇区 S${sec} · PCI ${c.new_pci}</div>
              ${this._renderPciQualityBlock(q, { compact: true })}
            </div>`;
          }).join('')}
        </div>
      `;
    });

    // 邻区按类型分组
    const nbrColors = MapManager.NBR_TYPE_COLORS;
    const totalNbrs = Object.values(result.nbr_counts || {}).reduce((a, b) => a + b, 0);
    html += `<h4 style="color: #ccc; margin: 12px 0 6px; font-size: 12px;">邻区规划结果 (${totalNbrs} 条)</h4>`;
    ['4G_4G', '4G_5G', '5G_4G', '5G_5G'].forEach(t => {
      const cnt = (result.nbr_counts || {})[t] || 0;
      const recs = (result.nbr_by_type || {})[t] || [];
      if (cnt === 0) return;
      const color = nbrColors[t];
      html += `
        <div class="info-card" style="border-left: 3px solid ${color}; margin-bottom: 4px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: bold; color: ${color};">${t}</span>
            <span style="color: #888;">${cnt} 条</span>
          </div>
          <div class="neighbor-list" style="max-height: 200px;">
            ${recs.slice(0, 50).map(n => `
              <div class="neighbor-item">
                <div class="name">${n.src_name || n.src_ecgi} <span style="color:#888;">→</span> ${n.dst_name || n.dst_ecgi}</div>
                <div class="meta">
                  ${(n.distance_m || 0).toFixed(0)}m | 得分 ${(n.score || 0).toFixed(2)}
                  ${n.same_freq ? '| <span style="color:#e6a23c;">同频</span>' : ''}
                  ${n.cross_system ? '| <span style="color:#f56c6c;">异系统</span>' : ''}
                </div>
              </div>
            `).join('')}
            ${recs.length > 50 ? `<div class="meta" style="text-align:center;">还有 ${recs.length - 50} 条…</div>` : ''}
          </div>
        </div>
      `;
    });

    document.getElementById('detail-header').textContent = `📋 规划结果 (${result.planned_cells.length}扇区 / ${totalNbrs}邻区)`;
    detail.innerHTML = html;
  },

  async exportSplit() {
    if (!this._lastPlannedEcgis || this._lastPlannedEcgis.length === 0) {
      this.log('请先做单站规划, 再导出分sheet xlsx', 'warn');
      return;
    }
    try {
      const { blob, filename } = await API.exportSplit(this._lastPlannedEcgis, this._lastNbrTypes);
      API.downloadBlob(blob, filename);
      this.log('导出完成: ' + filename, 'success');
    } catch (e) {
      this._logErr('单站规划导出', e, { ecgis: this._lastPlannedEcgis, nbr_types: this._lastNbrTypes });
    }
  },

  // ──────────────────────────────────────────────
  // 批量规划
  // ──────────────────────────────────────────────
  async planBatchAndExport() {
    const f = document.getElementById('batch-file').files[0];
    if (!f) {
      this.log('请先选择批量规划文件', 'warn');
      return;
    }
    const engine = document.querySelector('input[name="pci-engine"]:checked')?.value || 'legacy';
    const checkMod6 = !!document.getElementById('check-mod6')?.checked;
    const checkMod30 = !!document.getElementById('check-mod30')?.checked;
    const reuseKm = parseFloat(document.getElementById('reuse-distance')?.value || 5.0);
    const useBeam = !!document.getElementById('use-beam-overlap')?.checked;
    const planningMode = document.getElementById('batch-plan-mode')?.value || 'pci+nbr';
    const directionalFilter = !!document.getElementById('directional-filter')?.checked;

    this.log(`[批量规划] ${f.name} engine=${engine} mode=${planningMode}`, 'info');
    this._setBtnLoading('btn-batch-plan', true);
    this._progress.show('批量规划中…');
    this._progress.update(2, '正在上传文件…');
    try {
      const { blob, filename, stats } = await API.planBatch(f, {
        engine,
        reuse_distance_km: reuseKm,
        check_mod6: checkMod6,
        check_mod30: checkMod30,
        use_beam_overlap_score: useBeam,
        planning_mode: planningMode,
        directional_filter: directionalFilter,
      }, (pct, stage) => {
        this._progress.update(pct, stage);
      });
      API.downloadBlob(blob, filename);
      this.log(`[批量规划] 完成: ${stats.planned || '?'} 扇区, 截断=${stats.truncated}, 引擎=${stats.engine}`, 'success');
      this._progress.done(`完成 ${stats.planned || '?'} 扇区 · 已下载 ${filename}`);
      await this.refreshCells();
      this._setStatusWithSectors();
    } catch (e) {
      const msg = e?.message || String(e);
      this._progress.error(msg.slice(0, 60));
      if (msg.includes('504') || msg.includes('超时')) {
        this.log(`[批量规划] 超时: ${msg}`, 'error');
      } else {
        this._logErr('批量规划', e, { filename: f?.name });
      }
    } finally {
      this._setBtnLoading('btn-batch-plan', false);
    }
  },

  // ──────────────────────────────────────────────
  // 干扰分析
  // ──────────────────────────────────────────────
  async analyzeInterference() {
    const params = this._buildInterferenceParams();
    this.log(`[干扰分析] 距离${params.interference_distance_km}km overlap≥${params.overlap_threshold}%`, 'info');
    this._progress.show('干扰分析中…');
    this._progress.update(15, '正在计算扇区夹角与干扰…');
    this._setBtnLoading('btn-ia-analyze', true);
    // 活动心跳: 后端是同步阻塞, 前端拿不到真实进度, 用 setInterval 平滑推进 15→70%
    const startedAt = Date.now();
    const heartbeat = setInterval(() => {
      const elapsedSec = (Date.now() - startedAt) / 1000;
      const pct = Math.min(70, Math.round(15 + (elapsedSec / 30) * 55));
      const msg = elapsedSec < 10
        ? '正在计算扇区夹角与干扰…'
        : elapsedSec < 60
          ? '干扰分析进行中,请稍候…'
          : '仍在分析(数据量较大,可考虑缩小干扰距离)…';
      this._progress.update(pct, msg);
    }, 800);
    try {
      const r = await API.analyzeInterference(params, { timeoutMs: 300000 });
      clearInterval(heartbeat);
      this._progress.update(80, '正在渲染干扰连线…');
      // 从 issues 中提取冲突 ecgi (用于地图标红), 并叠加 cell_scores 中的高得分
      const conflictEcgis = new Set();
      (r.issues || []).forEach(it => {
        if (it.sector1?.ecgi) conflictEcgis.add(it.sector1.ecgi);
        if (it.sector2?.ecgi) conflictEcgis.add(it.sector2.ecgi);
      });
      // cell_scores 中 score>=40 (High/Critical) 的也标记
      (r.cell_scores || []).forEach(cs => {
        if (cs.score >= 40 && (cs.grade === 'Critical' || cs.grade === 'High')) {
          conflictEcgis.add(cs.ecgi);
        }
      });
      MapManager.clear();
      MapManager.renderCells(this.cells, conflictEcgis);
      MapManager.renderInterference(r.issues);
      this._setStatusWithSectors();
      const stats = r.stats || {};
      const truncatedNote = r.truncated
        ? `\n(结果已截断: 仅返回 ${stats.returned || 0} 条最严重的, 全部 ${stats.total || 0} 条请缩小干扰距离/区域/频段过滤后查看)`
        : '';
      const lines = [
        `共检出 ${stats.total || 0} 个干扰`,
        `  同频: ${stats.co_channel || 0} | 邻频: ${stats.adjacent_channel || 0}`,
        `  PCI collision: ${stats.pci_collision || 0} | mod3: ${stats.pci_mod3 || 0} | mod6: ${stats.pci_mod6 || 0}`,
        `严重度: Critical=${stats.critical || 0} High=${stats.high || 0} Medium=${stats.medium || 0} Low=${stats.low || 0}`,
        truncatedNote,
      ].filter(Boolean);
      document.getElementById('ia-stats').innerText = lines.join('\n');
      this.log(lines.join('\n'), stats.total > 0 ? 'warn' : 'success');
      // per-cell 评分表
      this._renderCellScoresTable(r.cell_scores || []);
      this._lastInterference = r;
      this._progress.done(`检出 ${stats.total || 0} 个干扰`);
    } catch (e) {
      clearInterval(heartbeat);
      const isAbort = e?.name === 'AbortError' || /aborted|timeout/i.test(e?.message || '');
      if (isAbort) {
        const msg = '干扰分析超时(>5分钟),请缩小干扰距离或减少参与分析的小区数';
        this._progress.error(msg);
        this.log('[干扰分析] ' + msg, 'error');
      } else {
        this._logErr('干扰分析', e, params);
        this._progress.error(e.message || '干扰分析失败');
      }
    } finally {
      this._setBtnLoading('btn-ia-analyze', false);
    }
  },

  async exportInterference() {
    const params = this._buildInterferenceParams();
    try {
      const { blob, filename } = await API.exportInterference(params);
      API.downloadBlob(blob, filename);
      this.log('干扰报告已导出: ' + filename, 'success');
    } catch (e) {
      this._logErr('干扰报告导出', e, params);
    }
  },

  _zoomToRat(rat) {
    const subset = this.cells.filter(c => c.rat === rat);
    if (subset.length === 0) {
      this.log(`地图上暂无 ${rat === 'LTE' ? '4G' : '5G'} 小区`, 'warn');
      return;
    }
    MapManager.fitBounds(subset);
    this.log(`聚焦显示 ${subset.length} 个 ${rat === 'LTE' ? '4G' : '5G'} 小区`, 'info');
  },

  _buildInterferenceParams() {
    const params = {
      interference_distance_km: parseFloat(document.getElementById('ia-distance').value || 5.0),
      overlap_threshold: parseFloat(document.getElementById('ia-overlap').value || 30.0),
      detect_co_channel: !!document.getElementById('ia-co')?.checked,
      detect_adjacent_channel: !!document.getElementById('ia-adj')?.checked,
      detect_pci_collision: !!document.getElementById('ia-collision')?.checked,
      detect_mod3: !!document.getElementById('ia-mod3')?.checked,
      detect_mod6: !!document.getElementById('ia-mod6')?.checked,
    };
    const rat = document.getElementById('ia-rat')?.value;
    if (rat) params.rat = rat;
    const fb = document.getElementById('ia-freqband')?.value;
    if (fb) params.freq_band = fb;
    const area = MapManager.getCurrentArea?.();
    if (area) params.area = area;
    return params;
  },

  /** 干扰分析: 网络制式切换时, 刷新频段下拉(只显示当前数据里出现过的频段) */
  _refreshIaFreqbandOptions() {
    const sel = document.getElementById('ia-freqband');
    const rat = document.getElementById('ia-rat')?.value || '';
    if (!sel) return;
    const freqSet = new Set();
    (this.cells || []).forEach(c => {
      if (rat && c.rat && c.rat !== rat) return;
      if (c.freq_band) freqSet.add(c.freq_band);
    });
    const list = [...freqSet].sort();
    sel.innerHTML = '<option value="" selected>全部频段</option>' +
      list.map(f => `<option value="${f}">${f}</option>`).join('');
  },

  /** PCI 规划卡片：按所选制式刷新频段下拉 */
  _refreshPciFreqbandOptions() {
    const sel = document.getElementById('pci-freqband');
    const rat = document.getElementById('pci-rat')?.value || '';
    if (!sel) return;
    const prev = sel.value;
    const freqSet = new Set();
    (this.cells || []).forEach(c => {
      if (rat && c.rat && c.rat !== rat) return;
      const fb = c.freq_band || c.freq_band_raw || c.freq_band_label;
      if (fb) freqSet.add(fb);
    });
    const list = [...freqSet].sort();
    sel.innerHTML = '<option value="">全部频段</option>' +
      list.map(f => `<option value="${f}">${f}</option>`).join('');
    if (prev && list.includes(prev)) sel.value = prev;
  },

  _pciScopeLabel() {
    const rat = document.getElementById('pci-rat')?.value || '';
    const fb = document.getElementById('pci-freqband')?.value || '';
    const parts = [];
    if (rat === 'LTE') parts.push('4G');
    else if (rat === 'NR') parts.push('5G');
    if (fb) parts.push(fb);
    return parts.length ? parts.join(' · ') : '全部制式/频段';
  },

  _cellMatchesPciScope(cell) {
    const rat = document.getElementById('pci-rat')?.value || '';
    const fb = document.getElementById('pci-freqband')?.value || '';
    if (rat && cell.rat && cell.rat !== rat) return false;
    if (fb) {
      const cfb = cell.freq_band || cell.freq_band_raw || cell.freq_band_label || '';
      if (cfb !== fb) return false;
    }
    return true;
  },

  _updateAreaInfo(area) {
    const info = document.getElementById('ia-area-info');
    if (!info) return;
    if (!area) { info.innerText = ''; return; }
    if (area.type === 'rect') {
      info.innerHTML = `已圈选矩形 → 约 ${this._kmBetween(area.lat1, area.lon1, area.lat2, area.lon2).toFixed(2)}×${this._kmBetween(area.lat1, area.lon1, area.lat2, area.lon2).toFixed(2)} km 见方`;
    } else if (area.type === 'circle') {
      info.innerHTML = `已圈选圆形 → 中心 ${area.lat.toFixed(4)},${area.lon.toFixed(4)} · 半径 <b>${area.radius_km.toFixed(2)} km</b>`;
    } else if (area.type === 'polygon') {
      const n = (area.points || []).length;
      const inPoly = this._cellsInArea(area).length;
      info.innerHTML = `已圈选多边形 → <b>${n}</b> 个顶点 · 范围内约 <b>${inPoly}</b> 个小区`;
    }
  },

  _updatePciAreaInfo(area) {
    const info = document.getElementById('pci-area-info');
    if (!info) return;
    if (!area) {
      info.innerText = '';
      return;
    }
    const n = this._cellsInArea(area).filter(c => this._cellMatchesPciScope(c)).length;
    const scope = this._pciScopeLabel();
    if (area.type === 'rect') {
      info.innerHTML = `选区内符合「${scope}」约 <b>${n}</b> 个 · 矩形 · 点「局部微调」`;
    } else if (area.type === 'circle') {
      info.innerHTML = `选区内符合「${scope}」约 <b>${n}</b> 个 · 圆 R=${area.radius_km.toFixed(2)}km`;
    } else if (area.type === 'polygon') {
      info.innerHTML = `选区内符合「${scope}」约 <b>${n}</b> 个 · 多边形 ${(area.points || []).length} 顶点`;
    }
  },

  _kmBetween(lat1, lon1, lat2, lon2) {
    const toRad = d => d * Math.PI / 180;
    const R = 6371;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  },

  /** 把 cell_scores 按得分降序渲染到 #ia-cell-scores 容器中(可滚动表格) */
  _renderCellScoresTable(scores) {
    const host = document.getElementById('ia-cell-scores');
    if (!host) return;
    if (!scores || scores.length === 0) {
      host.innerHTML = '<div style="color: var(--text-tertiary); font-size: 11px; padding: 4px;">无小区评分</div>';
      return;
    }
    // 数量大时分页渲染, 默认显示得分前 200
    const MAX_ROWS = 200;
    const sorted = [...scores].sort((a, b) => b.score - a.score).slice(0, MAX_ROWS);
    const rows = sorted.map(cs => {
      const same = cs.same_pci_min_km != null
        ? `${cs.same_pci_min_km.toFixed(2)}km<br><span style="color:#94a3b8;">→${(cs.same_pci_min_partner || '').slice(-12)}</span>`
        : '<span style="color:#94a3b8;">—</span>';
      const gradeColor = ({
        Critical: '#dc2626', High: '#f59e0b', Medium: '#3b82f6', Low: '#16a34a', Clean: '#94a3b8',
      })[cs.grade] || '#94a3b8';
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.06); cursor:pointer;"
                onclick="App.focusCell(${cs.lat}, ${cs.lon}, '${(cs.ecgi || '').replace(/'/g, "\\'")}')">
        <td style="padding:3px 4px;color:#cbd5e1;">${(cs.name || cs.ecgi).slice(-14)}</td>
        <td style="padding:3px 4px;color:#94a3b8;">${cs.rat}</td>
        <td style="padding:3px 4px;color:#cbd5e1;">${cs.freq_band || ''}</td>
        <td style="padding:3px 4px;color:#cbd5e1;">${cs.pci ?? ''}</td>
        <td style="padding:3px 4px;text-align:center;font-weight:600;color:${gradeColor}">
          ${cs.score} (${cs.grade})
        </td>
        <td style="padding:3px 4px;color:#facc15;">${same}</td>
        <td style="padding:3px 4px;color:#94a3b8;text-align:right;">${cs.issues_count || 0}</td>
      </tr>`;
    }).join('');
    host.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--text-secondary);margin:6px 0 4px;">
        <span>小区评分(前${sorted.length}/${scores.length}, 按 score 降序)</span>
        <span style="color:var(--text-tertiary);">点击行可在地图聚焦</span>
      </div>
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        <thead>
          <tr style="background:rgba(255,255,255,0.04);">
            <th style="padding:3px 4px;text-align:left;color:var(--text-secondary);">小区</th>
            <th style="padding:3px 4px;text-align:left;color:var(--text-secondary);">RAT</th>
            <th style="padding:3px 4px;text-align:left;color:var(--text-secondary);">频段</th>
            <th style="padding:3px 4px;text-align:left;color:var(--text-secondary);">PCI</th>
            <th style="padding:3px 4px;text-align:center;color:var(--text-secondary);">Score</th>
            <th style="padding:3px 4px;text-align:left;color:var(--text-secondary);">同PCI最近</th>
            <th style="padding:3px 4px;text-align:right;color:var(--text-secondary);">干扰数</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  },

  focusCell(lat, lon, ecgi) {
    if (!isFinite(lat) || !isFinite(lon)) return;
    if (window.MapManager && MapManager.map) {
      MapManager.map.setView([lat, lon], Math.max(MapManager.map.getZoom(), 16));
    }
    this.log(`[干扰分析] 已聚焦小区 ${ecgi}`, 'info');
  },

  async handleUpload(file) {
    if (!file) return;
    const append = !!document.getElementById('append-mode')?.checked;
    this.log(`上传文件: ${file.name} (模式: ${append ? '追加' : '替换'})`, 'info');
    try {
      const r = await API.uploadFile(file, { append });
      this.stats = r.stats;
      if (r.mode === 'append') {
        this.log(`追加完成: 新增/更新 ${r.added} 条, 当前共 ${r.total_after} 小区`, r.invalid_rows?.length > 0 ? 'warn' : 'success');
      } else {
        this.log(`导入完成: 有效${r.stats.valid}条, 异常${r.stats.invalid}条`, r.stats.invalid > 0 ? 'warn' : 'success');
      }
      if (r.invalid_rows && r.invalid_rows.length > 0) {
        this.log(`异常行号: ${r.invalid_rows.map(x => x.row).join(', ')}`, 'warn');
      }
      this.updateStatus();
      this.refreshCells();
    } catch (e) {
      this._logErr('上传文件', e, { filename: file?.name, size: file?.size });
    }
  },

  async loadSample() {
    this.log('加载示例数据...', 'info');
    try {
      const blob = await API.downloadSample();
      const file = new File([blob], 'sample_cells.xlsx');
      await this.handleUpload(file);
    } catch (e) {
      this._logErr('加载示例', e);
    }
  },

  async clearDatabase() {
    if (!confirm('确定要清空数据库吗?\n所有工参和规划结果将被永久删除, 无法恢复。')) return;
    this.log('清空数据库...', 'info');
    try {
      await API.clearDb();
      this.stats = {};
      await this.refreshCells();
      this.log('数据库已清空', 'success');
    } catch (e) {
      this._logErr('清空', e);
    }
  },

  async refreshCells() {
    try {
      const r = await API.getCells();
      this.cells = r.cells || [];
      this.stats = { ...this.stats, ...(r.stats || {}) };
      const conflictEcgis = new Set();
      MapManager.renderCells(this.cells, conflictEcgis);
      MapManager.fitBounds(this.cells);
      this.updateStatus();
      this._refreshIaFreqbandOptions();
      this._refreshPciFreqbandOptions();
      this._refreshPlanScope();
    } catch (e) {
      this._logErr('刷新小区列表', e);
    }
  },

  /** 解析 PCI 白/黑名单输入 (逗号分隔) */
  _parsePciList(inputId) {
    const raw = (document.getElementById(inputId)?.value || '').trim();
    if (!raw) return null;
    const nums = raw.split(/[,，\s]+/)
      .map(s => parseInt(s.trim(), 10))
      .filter(n => Number.isFinite(n) && n >= 0);
    return nums.length ? nums : null;
  },

  /** PCI 大屏 / 规划页共用的 PCI 规划参数 */
  _getPciPlanParams(extra = {}) {
    const engine = document.querySelector('input[name="pci-engine"]:checked')?.value || 'legacy';
    const reuseKm = parseFloat(document.getElementById('reuse-distance')?.value) || 5.0;
    const sceneEl = document.getElementById('scene-mode');
    const maxNbrEl = document.getElementById('max-neighbors');
    const planNeighbors = !!document.getElementById('plan-with-neighbors')?.checked;
    return {
      plan_neighbors: planNeighbors,
      engine,
      reuse_distance_km: reuseKm,
      check_mod6: !!document.getElementById('check-mod6')?.checked,
      check_mod30: document.getElementById('check-mod30')?.checked !== false,
      pci_whitelist: this._parsePciList('pci-whitelist'),
      pci_blacklist: this._parsePciList('pci-blacklist'),
      rat: document.getElementById('pci-rat')?.value || null,
      freq_band: document.getElementById('pci-freqband')?.value || null,
      directional_filter: document.getElementById('directional-filter')?.checked !== false,
      scene_mode: sceneEl?.value || 'land',
      max_neighbors: maxNbrEl ? (parseInt(maxNbrEl.value, 10) || 16) : 16,
      max_distance_km: parseFloat(document.getElementById('max-distance')?.value) || 5,
      weight_distance: parseFloat(document.getElementById('weight-distance')?.value) || 0.7,
      weight_overlap: parseFloat(document.getElementById('weight-overlap')?.value) || 0.3,
      score_threshold: parseFloat(document.getElementById('score-threshold')?.value) || 0.1,
      enable_cross_system: document.getElementById('enable-cross-system')?.checked !== false,
      enable_bidirectional: true,
      ...extra,
    };
  },

  async planAll() {
    if (this.cells.length === 0) {
      this.log('请先上传工参 (工参管理页导入)', 'warn');
      return;
    }
    const area = MapManager.getCurrentArea?.();
    if (area) {
      const n = this._cellsInArea(area).filter(c => this._cellMatchesPciScope(c)).length;
      const shape = area.type === 'circle' ? `圆形选区(R=${area.radius_km?.toFixed(2)}km)` : '矩形选区';
      const goLocal = confirm(
        `当前地图已有${shape}（符合当前网络/频段约 ${n} 个小区）。\n\n` +
        `• 点「确定」→ 按选区做【局部微调】（不会全网重算）\n` +
        `• 点「取消」→ 仍执行【全网规划】（忽略圈选，仅按上方 4G/频段 过滤）`,
      );
      if (goLocal) {
        this.log('[PCI] 已检测到圈选，改走局部微调（未点橙色按钮也会自动进入）', 'info');
        return this.planPartial();
      }
      this.log('[PCI] 已忽略地图圈选，执行全网规划', 'warn');
    }
    const params = this._getPciPlanParams();
    if (!params.rat) delete params.rat;
    if (!params.freq_band) delete params.freq_band;
    this.log(`[PCI] 全网规划 · ${this._pciScopeLabel()} · engine=${params.engine} 复用=${params.reuse_distance_km}km`, 'info');
    this._setBtnLoading('btn-plan-all', true);
    this._progress.show('全网 PCI 规划中…');
    this._progress.update(10, '正在分配 PCI…');
    try {
      const r = await API.planAll(params);
      const conflictCount = r.conflicts_count ?? r.stats?.conflict_count ?? 0;
      this.log(
        `PCI 规划完成: 小区 ${this.cells.length}, 冲突 ${conflictCount}` +
          (params.plan_neighbors ? `, 邻区 ${r.stats?.neighbor_stats?.neighbor_relations ?? '—'}` : ''),
        conflictCount > 0 ? 'warn' : 'success',
      );
      (r.log || []).slice(-30).forEach(line => this.log(line, 'info'));
      this.stats = { ...this.stats, ...(r.stats || {}), conflict_count: conflictCount };
      if (r.pci_quality) {
        this._mergePciQualityReport(r.pci_quality);
        const s = r.pci_quality.summary;
        if (s?.cells_reported) {
          this.log(`PCI 质量: 已评估 ${s.cells_reported} 扇区, 均分 ${s.avg_score}（越小越好）`, 'info');
        }
      }
      if (r.conflicts?.length) {
        this.conflicts = r.conflicts;
        const conflictEcgis = new Set();
        r.conflicts.forEach(c => {
          conflictEcgis.add(c.a.ecgi);
          conflictEcgis.add(c.b.ecgi);
        });
        await this.refreshCells();
        MapManager.renderCells(this.cells, conflictEcgis);
        MapManager.highlightConflicts(conflictEcgis);
      } else {
        await this.refreshCells();
        await this.checkConflict(true);
      }
      this._setStatusWithSectors();
      this._progress.done(`完成 · 冲突 ${conflictCount}`);
    } catch (e) {
      this._progress.error((e?.message || '规划失败').slice(0, 80));
      this._logErr('PCI 全网规划', e, params);
    } finally {
      this._setBtnLoading('btn-plan-all', false);
    }
  },

  async planPartial() {
    if (this.cells.length === 0) {
      this.log('请先上传工参', 'warn');
      return;
    }
    const area = MapManager.getCurrentArea();
    const reuseKm = parseFloat(document.getElementById('reuse-distance')?.value) || 5.0;

    let selected;
    let scopeLabel;
    const scopeHint = this._pciScopeLabel();
    if (area) {
      selected = this._cellsInArea(area)
        .filter(c => this._cellMatchesPciScope(c))
        .map(c => c.ecgi);
      if (area.type === 'rect') {
        scopeLabel = `矩形区(${selected.length}个小区)`;
      } else if (area.type === 'circle') {
        scopeLabel = `圆形区(中心 ${area.lat.toFixed(4)},${area.lon.toFixed(4)} 半径 ${area.radius_km.toFixed(2)}km, ${selected.length}个小区)`;
      } else if (area.type === 'polygon') {
        scopeLabel = `多边形区(${(area.points || []).length}顶点, ${selected.length}个小区)`;
      }
      if (selected.length === 0) {
        this.log(`当前选区内无符合「${scopeHint}」的小区, 请调整网络/频段或重画选区`, 'warn');
        return;
      }
    } else {
      const bounds = MapManager.map.getBounds();
      selected = this.cells
        .filter(c => bounds.contains([c.lat, c.lon]) && this._cellMatchesPciScope(c))
        .map(c => c.ecgi);
      scopeLabel = `当前可视范围(${selected.length}个小区)`;
      if (selected.length === 0) {
        this.log('请先在地图上缩放到包含小区的区域, 或画一个矩形/圆形选区', 'warn');
        return;
      }
      if (selected.length > 200) {
        selected = selected.slice(0, 200);
        scopeLabel = `可视范围前200(${selected.length}个小区)`;
      }
    }

    const params = this._getPciPlanParams({
      selected_ecgis: selected,
      radius_km: reuseKm,
    });
    if (!params.rat) delete params.rat;
    if (!params.freq_band) delete params.freq_band;
    this.log(`[PCI] 局部微调: ${scopeLabel} · ${scopeHint}, 辐射 ${reuseKm}km`, 'info');
    this._setBtnLoading('btn-plan-partial', true);
    this._progress.show('局部 PCI 微调…');
    this._progress.update(15, '重算选中范围 PCI…');
    try {
      const r = await API.planPartial(params);
      const conflictCount = r.conflicts_count ?? 0;
      this.log(`局部 PCI 完成: 影响 ${(r.affected || []).length} 小区, 冲突 ${conflictCount}`, conflictCount > 0 ? 'warn' : 'success');
      (r.log || []).slice(-20).forEach(line => this.log(line, 'info'));
      if (r.pci_quality) {
        this._mergePciQualityReport(r.pci_quality);
        const s = r.pci_quality.summary;
        if (s?.cells_reported) {
          this.log(`PCI 质量(局部): 评估 ${s.cells_reported} 扇区, 均分 ${s.avg_score}`, 'info');
        }
      }
      await this.refreshCells();
      if (r.conflicts?.length) {
        this.conflicts = r.conflicts;
        const conflictEcgis = new Set();
        r.conflicts.forEach(c => {
          conflictEcgis.add(c.a.ecgi);
          conflictEcgis.add(c.b.ecgi);
        });
        MapManager.renderCells(this.cells, conflictEcgis);
        MapManager.highlightConflicts(conflictEcgis);
      }
      this._setStatusWithSectors();
      this._progress.done(`完成 · 冲突 ${conflictCount}`);
    } catch (e) {
      this._progress.error((e?.message || '局部规划失败').slice(0, 80));
      this._logErr('PCI 局部规划', e, params);
    } finally {
      this._setBtnLoading('btn-plan-partial', false);
    }
  },

  _pointInPolygon(lat, lon, ring) {
    const pts = ring || [];
    if (pts.length < 3) return false;
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const latI = pts[i][0];
      const lonI = pts[i][1];
      const latJ = pts[j][0];
      const lonJ = pts[j][1];
      if (((latI > lat) !== (latJ > lat)) &&
        lon < (lonJ - lonI) * (lat - latI) / (latJ - latI + 1e-15) + lonI) {
        inside = !inside;
      }
    }
    return inside;
  },

  _cellsInArea(area) {
    if (!area) return this.cells;
    return this.cells.filter(c => {
      if (c.lat == null || c.lon == null) return false;
      if (area.type === 'rect') {
        return c.lat >= Math.min(area.lat1, area.lat2) && c.lat <= Math.max(area.lat1, area.lat2) &&
          c.lon >= Math.min(area.lon1, area.lon2) && c.lon <= Math.max(area.lon1, area.lon2);
      }
      if (area.type === 'circle') {
        return this._kmBetween(area.lat, area.lon, c.lat, c.lon) <= area.radius_km;
      }
      if (area.type === 'polygon') {
        return this._pointInPolygon(c.lat, c.lon, area.points);
      }
      return true;
    });
  },

  _refreshPlanScope() {
    const el = document.getElementById('plan-partial-scope');
    if (!el) return;
    const area = MapManager.getCurrentArea();
    const scope = this._pciScopeLabel();
    const partialBtn = document.getElementById('btn-plan-partial');
    if (!area) {
      el.innerHTML = `无圈选：点 <b style="color:var(--accent-orange,#e6a23c)">局部微调</b> = 可视范围(≤200) · <b>全网规划</b> = 全图 · 范围: ${scope}`;
      if (partialBtn) partialBtn.classList.remove('pci-partial-highlight');
      return;
    }
    const n = this._cellsInArea(area).filter(c => this._cellMatchesPciScope(c)).length;
    const shape = area.type === 'rect' ? '矩形'
      : area.type === 'polygon' ? `多边形(${(area.points || []).length}顶点)`
      : `圆 R=${area.radius_km.toFixed(2)}km`;
    el.innerHTML = `已圈选 ${shape} · 「${scope}」约 <b>${n}</b> 个 → 请点右侧 <b style="color:var(--accent-orange,#e6a23c)">局部微调</b>（蓝钮「全网规划」会忽略圈选）`;
    if (partialBtn) partialBtn.classList.add('pci-partial-highlight');
  },

  async checkConflict(silent = false) {
    if (!silent) this.log('执行冲突校验...', 'info');
    try {
      const directionalFilter = !!document.getElementById('directional-filter')?.checked;
      const r = await API.checkConflict(directionalFilter);
      this.conflicts = r.conflicts || [];
      const conflictEcgis = new Set();
      this.conflicts.forEach(c => {
        conflictEcgis.add(c.a.ecgi);
        conflictEcgis.add(c.b.ecgi);
      });
      MapManager.renderCells(this.cells, conflictEcgis);
      MapManager.highlightConflicts(conflictEcgis);
      this._setStatusWithSectors();
      this.log(`冲突总数: ${r.stats.total} (高${r.stats.high}, 中${r.stats.medium})`, r.stats.total > 0 ? 'warn' : 'success');
      // 显示弹窗
      if (!silent) this.showConflictModal();
    } catch (e) {
      this._logErr('冲突校验', e);
    }
  },

  async checkRedundancy() {
    this.log('执行冗余/漏配检测...', 'info');
    try {
      const r = await API.checkRedundancy();
      this.log(`冗余邻区: ${r.stats.redundant_count}, 漏配邻区: ${r.stats.missing_count}`, 'info');
      // 显示弹窗
      const body = document.getElementById('conflict-modal-body');
      body.innerHTML = `
        <h4 style="color: #e6a23c;">冗余邻区 (${r.stats.redundant_count})</h4>
        <div style="max-height: 200px; overflow-y: auto; margin-bottom: 16px;">
          ${r.redundant.map(x => `<div style="padding: 4px 0; font-size: 12px;">${x.src} -> ${x.dst}: ${x.reason}</div>`).join('') || '<div style="color: #888;">无</div>'}
        </div>
        <h4 style="color: #f56c6c;">漏配邻区 (${r.stats.missing_count})</h4>
        <div style="max-height: 200px; overflow-y: auto;">
          ${r.missing.map(x => `<div style="padding: 4px 0; font-size: 12px;">${x.a} <-> ${x.b}: ${x.reason}</div>`).join('') || '<div style="color: #888;">无</div>'}
        </div>
      `;
      document.getElementById('conflict-modal-title').textContent = '冗余/漏配检测';
      document.getElementById('conflict-modal').style.display = 'flex';
    } catch (e) {
      this._logErr('冗余检测', e);
    }
  },

  showConflictModal() {
    const body = document.getElementById('conflict-modal-body');
    if (this.conflicts.length === 0) {
      body.innerHTML = '<div style="color: #67c23a; padding: 20px; text-align: center;">无冲突</div>';
    } else {
      body.innerHTML = `
        <table style="width:100%; border-collapse: collapse; font-size: 12px;">
          <thead>
            <tr style="background: #1a1a1a;">
              <th style="padding: 6px; text-align: left; color: #ccc;">小区A</th>
              <th style="padding: 6px; color: #ccc;">PCI</th>
              <th style="padding: 6px; text-align: left; color: #ccc;">小区B</th>
              <th style="padding: 6px; color: #ccc;">PCI</th>
              <th style="padding: 6px; color: #ccc;">类型</th>
              <th style="padding: 6px; color: #ccc;">严重度</th>
            </tr>
          </thead>
          <tbody>
            ${this.conflicts.map(c => `
              <tr style="border-bottom: 1px solid #3a3a3a;">
                <td style="padding: 6px; color: #e0e0e0;">${c.a.name}</td>
                <td style="padding: 6px; color: #409eff;">${c.a.pci}</td>
                <td style="padding: 6px; color: #e0e0e0;">${c.b.name}</td>
                <td style="padding: 6px; color: #409eff;">${c.b.pci}</td>
                <td style="padding: 6px; color: #e6a23c;">${c.type}</td>
                <td style="padding: 6px; color: ${c.severity === 'high' ? '#f56c6c' : '#e6a23c'};">${c.severity === 'high' ? '高' : '中'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }
    document.getElementById('conflict-modal-title').textContent = `冲突清单 (${this.conflicts.length})`;
    document.getElementById('conflict-modal').style.display = 'flex';
  },

  async exportFile(type) {
    this.log(`导出 ${type}...`, 'info');
    try {
      const { blob, filename } = await API.exportFile(type);
      API.downloadBlob(blob, filename);
      this.log(`已导出: ${filename}`, 'success');
    } catch (e) {
      this._logErr('导出文件', e, { type });
    }
  },

  async exportMML() {
    const vendor = document.getElementById('mml-vendor').value;
    this.log(`导出 ${vendor} MML脚本...`, 'info');
    try {
      const { blob, filename } = await API.exportFile('mml', vendor);
      API.downloadBlob(blob, filename);
      this.log(`已导出: ${filename}`, 'success');
    } catch (e) {
      this._logErr('导出MML', e, { vendor });
    }
  },

  async showCellDetail(cell) {
    const body = document.getElementById('detail-body');
    const header = document.getElementById('detail-header');
    if (header) header.textContent = cell.name || cell.ecgi || '小区详情';
    let q = cell.pci_quality || this.pciQualityByEcgi[cell.ecgi];
    const pciVal = cell.new_pci ?? cell.pci;
    if (!q && pciVal != null && this.cells.length > 0) {
      try {
        const r = await API.pciQuality({
          ecgis: [cell.ecgi],
          check_mod30: document.getElementById('check-mod30')?.checked !== false,
          directional_filter: document.getElementById('directional-filter')?.checked !== false,
        });
        const one = r.pci_quality?.cells?.[0];
        if (one) {
          q = one;
          this.pciQualityByEcgi[cell.ecgi] = one;
        }
      } catch (_) { /* 静默 */ }
    }
    const qualitySection = q
      ? `<h4 style="color: #ccc; margin: 12px 0 6px; font-size: 12px;">PCI 质量与干扰</h4><div class="info-card" style="border-left:3px solid ${this._qualityLabelColor(q.quality_label)};">${this._renderPciQualityBlock(q)}</div>`
      : (pciVal != null
        ? '<div style="font-size:11px;color:#888;margin-top:8px;">暂无 PCI 质量数据，请先执行 PCI 规划</div>'
        : '');
    body.innerHTML = `
      <div class="info-card">
        <div class="row"><div class="key">ECGI</div><div class="val">${cell.ecgi}</div></div>
        <div class="row"><div class="key">小区名</div><div class="val">${cell.name || '-'}</div></div>
        <div class="row"><div class="key">制式</div><div class="val">${cell.rat}</div></div>
        <div class="row"><div class="key">频点</div><div class="val">${cell.earfcn || '-'}</div></div>
        <div class="row"><div class="key">经纬度</div><div class="val">${cell.lon}, ${cell.lat}</div></div>
        <div class="row"><div class="key">方位角</div><div class="val">${cell.azimuth}°</div></div>
        <div class="row"><div class="key">覆盖半径</div><div class="val">${cell.radius}m</div></div>
        <div class="row"><div class="key">TAC</div><div class="val">${cell.tac || '-'}</div></div>
        <div class="row"><div class="key">原PCI</div><div class="val">${cell.pci}</div></div>
        <div class="row"><div class="key">新PCI</div><div class="val" style="color: #67c23a; font-weight: 600;">${cell.new_pci}</div></div>
        <div class="row"><div class="key">站点</div><div class="val">${cell.site_name || '-'}</div></div>
        <div class="row"><div class="key">邻区数</div><div class="val">${(cell.neighbors || []).length}</div></div>
      </div>
      ${qualitySection}
      <h4 style="color: #ccc; margin: 8px 0 6px; font-size: 12px;">邻区列表 (${(cell.neighbors || []).length})</h4>
      <div class="neighbor-list">
        ${(cell.neighbors || []).map(n => `
          <div class="neighbor-item">
            <div class="name">${n.dst_name}<span class="score">${n.score}</span></div>
            <div class="meta">
              ${n.distance_m.toFixed(0)}m | 交叠${n.overlap_m2.toFixed(0)}m²
              ${n.same_freq ? '| 同频' : ''}
              ${n.cross_system ? '| 异系统' : ''}
              ${n.auto_added ? '| <span style="color:#e6a23c;">自动补齐</span>' : ''}
            </div>
          </div>
        `).join('') || '<div style="color: #888; font-size: 12px;">暂无邻区</div>'}
      </div>
    `;
    // 渲染邻区连线
    MapManager.renderNeighborLines(cell.ecgi);
  },

  updateStatus() {
    const total = this.cells.length;
    const ratCounts = this.stats.rat_counts || {};
    const conflictCount = this.stats.conflict_count || 0;
    document.getElementById('status-line').textContent =
      total > 0
        ? `已加载 ${total} 小区 | LTE:${ratCounts.LTE||0} NR:${ratCounts.NR||0} | 冲突:${conflictCount}`
        : '未加载数据';
  },

  _setStatusWithSectors() {
    const total = this.cells.length;
    const ratCounts = this.stats.rat_counts || {};
    const conflictCount = this.stats.conflict_count || 0;
    document.getElementById('status-line').textContent =
      total > 0
        ? `已加载 ${total} 小区 | LTE:${ratCounts.LTE||0} NR:${ratCounts.NR||0} | 冲突:${conflictCount} | 扇区已渲染`
        : '未加载数据';
  },

  _setBtnLoading(id, loading) {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (loading) {
      btn.dataset.originalText = btn.textContent;
      btn.textContent = '处理中…';
      btn.disabled = true;
      btn.classList.add('btn-loading');
    } else {
      btn.textContent = btn.dataset.originalText || btn.textContent;
      btn.disabled = false;
      btn.classList.remove('btn-loading');
    }
  },

  // ──────────────────────────────────────────────
  // 规划进度条控制器 (地图底部高亮进度条)
  // ──────────────────────────────────────────────
  _progress: {
    el: null, fillEl: null, pctEl: null, titleEl: null, stageEl: null,
    hideTimer: null,
    ensure() {
      if (this.el) return;
      this.el = document.getElementById('plan-progress');
      this.fillEl = document.getElementById('pp-fill');
      this.pctEl = document.getElementById('pp-percent');
      this.titleEl = document.getElementById('pp-title');
      this.stageEl = document.getElementById('pp-stage');
    },
    show(title = '规划中…') {
      this.ensure();
      if (!this.el) return;
      clearTimeout(this.hideTimer);
      this.el.classList.remove('done', 'error', 'fade-out');
      this.el.classList.add('visible');
      this.titleEl.textContent = title;
      this.stageEl.textContent = '正在准备…';
      this.fillEl.style.width = '0%';
      this.pctEl.textContent = '0%';
    },
    update(pct, stage = null) {
      this.ensure();
      if (!this.el) return;
      const p = Math.max(0, Math.min(100, Number(pct) || 0));
      this.fillEl.style.width = p + '%';
      this.pctEl.textContent = p + '%';
      if (stage) this.stageEl.textContent = stage;
    },
    done(stage = '完成') {
      this.ensure();
      if (!this.el) return;
      this.fillEl.style.width = '100%';
      this.pctEl.textContent = '100%';
      this.stageEl.textContent = stage;
      this.titleEl.textContent = '✓ 规划完成';
      this.el.classList.add('done');
      // 1.2s 后淡出
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => this.hide(), 1200);
    },
    error(stage = '失败') {
      this.ensure();
      if (!this.el) return;
      this.titleEl.textContent = '✗ 规划失败';
      this.stageEl.textContent = stage;
      this.el.classList.add('error');
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => this.hide(), 2500);
    },
    hide() {
      if (!this.el) return;
      this.el.classList.add('fade-out');
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => {
        this.el.classList.remove('visible', 'fade-out', 'done', 'error');
      }, 350);
    },
  },

  log(message, level = 'info', extra = null) {
    const panel = document.getElementById('log-panel');
    if (!panel) return;
    const line = document.createElement('div');
    line.className = `log-line ${level}`;
    const ts = new Date().toLocaleTimeString();
    let text = `[${ts}] ${message}`;
    if (extra) {
      text += '\n  ' + (typeof extra === 'string' ? extra : JSON.stringify(extra, null, 2))
                  .split('\n').join('\n  ');
    }
    line.textContent = text;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
    // 限制最多 2000 行
    while (panel.children.length > 2000) {
      panel.removeChild(panel.firstChild);
    }
    // 持久化到 localStorage (最近 500 条, 仅文本避免膨胀)
    try {
      const entry = { ts, level, msg: message, extra: extra || null };
      const arr = JSON.parse(localStorage.getItem('app_logs') || '[]');
      arr.push(entry);
      while (arr.length > 500) arr.shift();
      localStorage.setItem('app_logs', JSON.stringify(arr));
    } catch (e) { /* localStorage 不可用时静默 */ }
  },

  /** 恢复 localStorage 中的历史日志 */
  restoreLogs() {
    try {
      const arr = JSON.parse(localStorage.getItem('app_logs') || '[]');
      if (!arr.length) return;
      const panel = document.getElementById('log-panel');
      arr.forEach(entry => {
        const line = document.createElement('div');
        line.className = `log-line ${entry.level || 'info'}`;
        let text = `[${entry.ts}] ${entry.msg}`;
        if (entry.extra) {
          text += '\n  ' + (typeof entry.extra === 'string' ? entry.extra : JSON.stringify(entry.extra, null, 2))
                      .split('\n').join('\n  ');
        }
        line.textContent = text;
        panel.appendChild(line);
      });
      panel.scrollTop = panel.scrollHeight;
    } catch (e) { /* ignore */ }
  },

  /** 复制全部日志到剪贴板 */
  copyLogs() {
    const panel = document.getElementById('log-panel');
    const lines = Array.from(panel.children).map(el => el.textContent);
    const text = lines.join('\n');
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).catch(fallback);
    } else {
      fallback();
    }
    this.log(`已复制 ${lines.length} 行日志到剪贴板`, 'success');
  },

  /** 清空日志 (DOM + localStorage) */
  clearLogs() {
    const panel = document.getElementById('log-panel');
    while (panel.firstChild) panel.removeChild(panel.firstChild);
    try { localStorage.removeItem('app_logs'); } catch (e) {}
  },

  /** 统一错误记录: 把 message + payload + stack 写到日志, 便于定位 bug */
  _logErr(action, e, payload = null) {
    const detail = {
      action,
      message: e?.message || String(e),
      stack: e?.stack || null,
      payload,
      ua: navigator.userAgent,
      url: location.href,
    };
    this.log(`${action}失败: ${detail.message}`, 'error', detail);
  },
};

window.App = App;
document.addEventListener('DOMContentLoaded', () => App.init());
// 工参管理页面逻辑（导入 + 增删改）
(function () {
  'use strict';

  /** @type {File[]} */
  let currentFiles = [];
  /** @type {File|null} */
  let bulkUpdateFile = null;
  let currentCells = [];
  let currentStats = {};
  let currentPage = 1;
  const pageSize = 50;
  let importHistory = [];
  let cellModalMode = 'create'; // create | edit
  let editingEcgi = null;

  const SYNC_BC = typeof BroadcastChannel !== 'undefined'
    ? new BroadcastChannel('wybb-cells-sync')
    : null;

  function onExternalCellsSync(source) {
    log(`检测到网管基础数据已同步（${source}），正在刷新工参列表…`, 'success');
    loadStats();
    loadCells();
  }

  // 初始化
  document.addEventListener('DOMContentLoaded', function () {
    initFileUpload();
    initBulkUpdate();
    initTabs();
    initCellModal();
    loadStats();
    loadCells();

    if (SYNC_BC) {
      SYNC_BC.onmessage = (ev) => {
        if (ev.data && ev.data.type === 'cells-sync-done') {
          onExternalCellsSync('网管数据页');
        }
      };
    }
    window.addEventListener('storage', (ev) => {
      if (ev.key === 'wybb_cells_sync_at' && ev.newValue) {
        onExternalCellsSync('其他标签页');
      }
    });
  });

  // ========== 文件上传 ==========
  function initFileUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      if (e.dataTransfer.files.length) {
        addFiles(e.dataTransfer.files);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length) {
        addFiles(e.target.files);
        fileInput.value = '';
      }
    });

    document.getElementById('file-clear-all').addEventListener('click', () => {
      clearSelectedFiles();
    });

    document.getElementById('file-list').addEventListener('click', (e) => {
      const rm = e.target.closest('[data-remove-index]');
      if (!rm) return;
      const idx = parseInt(rm.dataset.removeIndex, 10);
      if (!Number.isFinite(idx)) return;
      currentFiles.splice(idx, 1);
      renderFileList();
      if (currentFiles.length === 0) {
        document.getElementById('import-result').style.display = 'none';
      }
      log('已移除文件', 'info');
    });

    // 导入按钮
    document.getElementById('btn-upload').addEventListener('click', () => {
      if (currentFiles.length === 0) {
        fileInput.click();
      } else {
        doUpload();
      }
    });

    // 示例数据
    document.getElementById('btn-sample').addEventListener('click', loadSample);

    // 清空数据库
    document.getElementById('btn-clear-db').addEventListener('click', clearDatabase);

    // 刷新
    document.getElementById('btn-refresh-stats').addEventListener('click', () => {
      loadStats();
      loadCells();
    });

    // 模板下载
    document.getElementById('btn-template-4g').addEventListener('click', () => {
      API.downloadTemplate('4G').then(({ blob, filename }) => {
        API.downloadBlob(blob, filename);
        log('已下载 4G 模板', 'success');
      }).catch(e => log('模板下载失败: ' + e.message, 'error'));
    });
    document.getElementById('btn-template-5g').addEventListener('click', () => {
      API.downloadTemplate('5G').then(({ blob, filename }) => {
        API.downloadBlob(blob, filename);
        log('已下载 5G 模板', 'success');
      }).catch(e => log('模板下载失败: ' + e.message, 'error'));
    });
    document.getElementById('btn-template-both').addEventListener('click', () => {
      API.downloadTemplate('both').then(({ blob, filename }) => {
        API.downloadBlob(blob, filename);
        log('已下载双制式模板', 'success');
      }).catch(e => log('模板下载失败: ' + e.message, 'error'));
    });

    // 导出
    document.getElementById('btn-export-workparams').addEventListener('click', () => exportFile('workparams'));
    document.getElementById('btn-export-neighbors').addEventListener('click', () => exportFile('neighbors'));
    document.getElementById('btn-export-conflicts').addEventListener('click', () => exportFile('conflicts'));
    document.getElementById('btn-export-summary').addEventListener('click', () => exportFile('summary'));

    // 小区搜索和筛选
    document.getElementById('cells-search').addEventListener('input', () => {
      currentPage = 1;
      renderCellsTable();
    });
    document.getElementById('filter-rat').addEventListener('change', () => {
      currentPage = 1;
      renderCellsTable();
    });
    document.getElementById('filter-sync').addEventListener('change', () => {
      currentPage = 1;
      renderCellsTable();
    });
    document.getElementById('btn-refresh-cells').addEventListener('click', loadCells);

    document.getElementById('btn-add-cell').addEventListener('click', () => openCellModal('create'));
  }

  function initBulkUpdate() {
    const zone = document.getElementById('bulk-update-zone');
    const input = document.getElementById('bulk-update-input');
    const nameEl = document.getElementById('bulk-update-filename');
    if (!zone || !input) return;

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) setBulkUpdateFile(f);
    });

    input.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) setBulkUpdateFile(f);
      input.value = '';
    });

    function setBulkUpdateFile(file) {
      if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
        log('更新文件仅支持 .xlsx / .xls / .csv', 'warn');
        return;
      }
      bulkUpdateFile = file;
      if (nameEl) nameEl.textContent = file.name;
      log('已选择更新文件: ' + file.name, 'info');
    }

    document.getElementById('btn-bulk-update-template').addEventListener('click', () => {
      API.downloadBulkUpdateTemplate()
        .then(({ blob, filename }) => {
          API.downloadBlob(blob, filename);
          log('已下载文件更新模板', 'success');
        })
        .catch((e) => log('模板下载失败: ' + e.message, 'error'));
    });

    document.getElementById('btn-bulk-update').addEventListener('click', () => {
      if (!bulkUpdateFile) {
        input.click();
        return;
      }
      doBulkUpdate();
    });
  }

  async function doBulkUpdate() {
    if (!bulkUpdateFile) {
      log('请先选择更新文件', 'warn');
      return;
    }
    const btn = document.getElementById('btn-bulk-update');
    btn.disabled = true;
    btn.classList.add('btn-loading');
    try {
      const result = await API.workparamsBulkUpdate(bulkUpdateFile);
      renderBulkUpdateResult(result);
      const u = result.updated || 0;
      const nf = result.not_found_count || 0;
      log(`文件更新完成: 成功 ${u} 条，未匹配 ${nf} 条`, u > 0 ? 'success' : 'warn');
      if (u > 0) {
        await loadStats();
        await loadCells();
      }
    } catch (e) {
      log('文件更新失败: ' + e.message, 'error');
      const panel = document.getElementById('bulk-update-panel');
      const wrap = document.getElementById('bulk-update-result');
      if (panel && wrap) {
        wrap.style.display = 'block';
        panel.innerHTML = `<div class="result-item error">✗ ${escapeHtml(e.message)}</div>`;
      }
    } finally {
      btn.disabled = false;
      btn.classList.remove('btn-loading');
    }
  }

  function renderBulkUpdateResult(result) {
    const wrap = document.getElementById('bulk-update-result');
    const panel = document.getElementById('bulk-update-panel');
    if (!wrap || !panel) return;
    wrap.style.display = 'block';

    const ps = result.parse_stats || {};
    const inv = result.invalid_rows || [];
    let html = '';
    html += `<div class="result-item success">✓ 已更新 <strong>${result.updated || 0}</strong> 条小区</div>`;
    html += `<div class="result-item">解析: 有效行 ${ps.valid ?? '—'} / 共 ${ps.total ?? '—'}，格式错误 ${ps.invalid ?? 0}</div>`;
    if (result.not_found_count > 0) {
      html += `<div class="result-item warn">未匹配工参: ${result.not_found_count} 条</div>`;
      const sample = (result.not_found_ecgis || result.not_found || []).slice(0, 8);
      if (sample.length) {
        html += `<div class="result-item" style="font-size:10px;color:#888;">示例: ${escapeHtml(sample.join(', '))}${result.not_found_count > 8 ? '…' : ''}</div>`;
      }
    }
    if (result.skipped_duplicate_ecgi > 0) {
      html += `<div class="result-item warn">文件中重复 CGI 已跳过: ${result.skipped_duplicate_ecgi}</div>`;
    }
    if (result.skipped_pci_invalid > 0) {
      html += `<div class="result-item warn">PCI 与制式范围不符已跳过: ${result.skipped_pci_invalid}</div>`;
    }
    if (inv.length > 0) {
      html += `<div class="result-item error">行级错误 ${inv.length} 条（最多展示 5 条）</div>`;
      inv.slice(0, 5).forEach((row) => {
        const errs = (row.errors || []).join('; ');
        html += `<div class="result-item" style="font-size:10px;">第 ${row.row} 行 ${escapeHtml(row.ecgi || '')}: ${escapeHtml(errs)}</div>`;
      });
    }
    panel.innerHTML = html;
  }

  function initCellModal() {
    const overlay = document.getElementById('cell-modal');
    const close = () => {
      overlay.style.display = 'none';
      editingEcgi = null;
    };
    document.getElementById('cell-modal-close').addEventListener('click', close);
    document.getElementById('cell-modal-cancel').addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });

    document.getElementById('cell-form').addEventListener('submit', (e) => {
      e.preventDefault();
      submitCellForm();
    });
  }

  function openCellModal(mode, row) {
    cellModalMode = mode;
    editingEcgi = mode === 'edit' && row ? row.ecgi : null;
    const title = document.getElementById('cell-modal-title');
    const ecgiInput = document.getElementById('cf-ecgi');
    title.textContent = mode === 'edit' ? '编辑小区' : '新增小区';
    ecgiInput.disabled = mode === 'edit';

    const setVal = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (v == null || v === '') {
        el.value = '';
      } else {
        el.value = v;
      }
    };

    if (mode === 'edit' && row) {
      setVal('cf-ecgi', row.ecgi);
      setVal('cf-name', row.name);
      setVal('cf-rat', row.rat || 'LTE');
      setVal('cf-site-type', row.site_type || '陆地');
      setVal('cf-lon', row.lon);
      setVal('cf-lat', row.lat);
      setVal('cf-azimuth', row.azimuth != null ? row.azimuth : 0);
      setVal('cf-pci', row.pci != null && row.pci >= 0 ? row.pci : '');
      setVal('cf-earfcn', row.earfcn);
      setVal('cf-tac', row.tac);
      setVal('cf-bandwidth', row.bandwidth);
      setVal('cf-phy-name', row.phy_name);
      setVal('cf-ant-name', row.ant_name);
      setVal('cf-manufacturer', row.manufacturer);
      setVal('cf-oms-name', row.oms_name);
      setVal('cf-freq-band-raw', row.freq_band_label || row.freq_band || '');
    } else {
      document.getElementById('cell-form').reset();
      document.getElementById('cf-rat').value = 'LTE';
      document.getElementById('cf-site-type').value = '陆地';
      document.getElementById('cf-azimuth').value = '0';
      ecgiInput.disabled = false;
    }

    document.getElementById('cell-modal').style.display = 'flex';
  }

  function readCellFormPayload() {
    const numOrNull = (id) => {
      const v = document.getElementById(id).value.trim();
      if (v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const strOrNull = (id) => {
      const v = document.getElementById(id).value.trim();
      return v === '' ? null : v;
    };

    const payload = {
      ecgi: document.getElementById('cf-ecgi').value.trim(),
      name: document.getElementById('cf-name').value.trim(),
      rat: document.getElementById('cf-rat').value,
      site_type: document.getElementById('cf-site-type').value,
      lon: numOrNull('cf-lon'),
      lat: numOrNull('cf-lat'),
      azimuth: numOrNull('cf-azimuth') ?? 0,
      pci: numOrNull('cf-pci'),
      earfcn: numOrNull('cf-earfcn'),
      tac: numOrNull('cf-tac'),
      bandwidth: numOrNull('cf-bandwidth'),
      phy_name: strOrNull('cf-phy-name'),
      ant_name: strOrNull('cf-ant-name'),
      manufacturer: strOrNull('cf-manufacturer'),
      oms_name: strOrNull('cf-oms-name'),
      freq_band_raw: strOrNull('cf-freq-band-raw'),
    };
    return payload;
  }

  async function submitCellForm() {
    const saveBtn = document.getElementById('cell-modal-save');
    const payload = readCellFormPayload();
    if (!payload.name || payload.lon == null || payload.lat == null) {
      log('请填写必填项：小区名称、经纬度', 'warn');
      return;
    }
    if (cellModalMode === 'create' && !payload.ecgi) {
      log('请填写 ECGI', 'warn');
      return;
    }

    saveBtn.disabled = true;
    saveBtn.classList.add('btn-loading');
    try {
      if (cellModalMode === 'edit' && editingEcgi) {
        const { ecgi, ...updates } = payload;
        await API.updateCell(editingEcgi, updates);
        log(`已更新小区: ${editingEcgi}`, 'success');
      } else {
        await API.createCell(payload);
        log(`已新增小区: ${payload.ecgi}`, 'success');
      }
      document.getElementById('cell-modal').style.display = 'none';
      editingEcgi = null;
      await loadCells();
    } catch (err) {
      log((cellModalMode === 'edit' ? '更新失败: ' : '新增失败: ') + err.message, 'error');
    } finally {
      saveBtn.disabled = false;
      saveBtn.classList.remove('btn-loading');
    }
  }

  async function deleteCellRow(row) {
    const label = row.name || row.ecgi;
    if (!confirm(`确定删除小区「${label}」？\nECGI: ${row.ecgi}\n删除后不可恢复。`)) return;
    try {
      await API.deleteCell(row.ecgi);
      log(`已删除: ${label}`, 'success');
      await loadCells();
    } catch (e) {
      log('删除失败: ' + e.message, 'error');
    }
  }

  function guessRatFromFilename(name) {
    const fn = (name || '').toLowerCase();
    if (/5g|nr|gnodeb|gnb/.test(fn) && !/4g|lte/.test(fn)) return { kind: '5G', label: '5G（文件名）' };
    if (/4g|lte|enodeb|eutran/.test(fn) && !/5g|nr/.test(fn)) return { kind: '4G', label: '4G（文件名）' };
    if (/5g|nr/.test(fn) && /4g|lte/.test(fn)) return { kind: 'mixed', label: '4G+5G（文件名）' };
    return { kind: 'unknown', label: '待识别' };
  }

  function ratTagClass(kind) {
    if (kind === '4G') return 'rat-4g';
    if (kind === '5G') return 'rat-5g';
    if (kind === 'mixed') return 'rat-mixed';
    return 'rat-unknown';
  }

  function addFiles(fileList) {
    const added = [];
    for (const file of fileList) {
      if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
        log(`已跳过非工参文件: ${file.name}`, 'warn');
        continue;
      }
      const dup = currentFiles.some(
        (f) => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified
      );
      if (dup) continue;
      currentFiles.push(file);
      added.push(file);
    }
    if (added.length === 0 && fileList.length > 0) {
      log('没有可添加的新文件', 'warn');
      return;
    }
    renderFileList();
    const names = added.map((f) => f.name).join(', ');
    log(`已选择 ${currentFiles.length} 个文件${names ? ': ' + names : ''}`, 'info');
  }

  function clearSelectedFiles() {
    currentFiles = [];
    document.getElementById('file-input').value = '';
    renderFileList();
    document.getElementById('import-result').style.display = 'none';
    log('已清空待导入文件', 'info');
  }

  function renderFileList() {
    const listEl = document.getElementById('file-list');
    const clearBtn = document.getElementById('file-clear-all');
    const uploadBtn = document.getElementById('btn-upload');
    if (currentFiles.length === 0) {
      listEl.style.display = 'none';
      listEl.innerHTML = '';
      clearBtn.style.display = 'none';
      uploadBtn.textContent = '开始导入';
      return;
    }
    listEl.style.display = 'block';
    clearBtn.style.display = 'inline-block';
    uploadBtn.textContent = currentFiles.length > 1
      ? `开始导入 (${currentFiles.length} 个文件)`
      : '开始导入';

    let html = '';
    currentFiles.forEach((file, i) => {
      const g = guessRatFromFilename(file.name);
      html += `<div class="file-info">` +
        `<span class="name">` +
        `<span class="rat-tag ${ratTagClass(g.kind)}">${escapeAttr(g.label)}</span>` +
        `<span style="overflow:hidden;text-overflow:ellipsis;">${escapeAttr(file.name)}</span>` +
        `<span style="color:#666;margin-left:6px;flex-shrink:0;">${formatSize(file.size)}</span>` +
        `</span>` +
        `<span class="remove" data-remove-index="${i}" title="移除">✕</span>` +
        `</div>`;
    });
    listEl.innerHTML = html;
  }

  function doUpload() {
    if (currentFiles.length === 0) {
      log('请先选择文件', 'warn');
      return;
    }
    const append = document.getElementById('append-mode').checked;
    const btn = document.getElementById('btn-upload');
    btn.classList.add('btn-loading');
    btn.disabled = true;

    const modeLabel = append ? '追加' : '替换';
    log(
      `开始导入 ${currentFiles.length} 个文件 (模式: ${modeLabel})`,
      'info'
    );

    API.uploadFiles(currentFiles, { append }).then(r => {
      currentStats = r.stats;
      renderImportResult(r, append);
      updateStats();
      loadCells();
      if (r.files && r.files.length > 1) {
        r.files.forEach((f) => {
          if (!f.success) {
            log(`${f.filename}: 失败 — ${f.error || '未知错误'}`, 'error');
            return;
          }
          const p = f.rat_profile || {};
          log(
            `${f.filename} → ${p.label || '已识别'} (有效 ${(f.stats && f.stats.valid) || 0} 条)`,
            'success'
          );
        });
      }
      const totalValid = r.stats?.valid ?? r.cells_count ?? 0;
      const invalid = r.stats?.invalid ?? 0;
      if (append && r.total_after != null) {
        log(`导入完成: 当前共 ${r.total_after} 小区`, invalid > 0 ? 'warn' : 'success');
      } else {
        log(`导入完成: 有效 ${totalValid} 条, 异常 ${invalid} 条`, invalid > 0 ? 'warn' : 'success');
      }
      if (r.invalid_rows && r.invalid_rows.length > 0) {
        const rows = r.invalid_rows.slice(0, 20).map((x) => {
          const src = x.source_file ? `${x.source_file}:` : '';
          return src + x.row;
        });
        log(`异常行: ${rows.join(', ')}${r.invalid_rows.length > 20 ? ' …' : ''}`, 'warn');
      }
      clearSelectedFiles();
    }).catch(err => {
      log('导入失败: ' + err.message, 'error');
    }).finally(() => {
      btn.classList.remove('btn-loading');
      btn.disabled = false;
    });
  }

  function renderImportResult(r, append) {
    const panel = document.getElementById('result-panel');
    const resultDiv = document.getElementById('import-result');
    resultDiv.style.display = 'block';

    let html = '<div style="font-weight:600; color:#e0e0e0; margin-bottom:8px;">导入结果</div>';

    if (r.files && r.files.length > 0) {
      html += '<div style="font-size:11px;color:#888;margin-bottom:8px;">各文件识别</div>';
      r.files.forEach((f) => {
        const p = f.rat_profile || {};
        const tagClass = ratTagClass(p.kind || 'unknown');
        if (f.success) {
          html += `<div class="result-item success">` +
            `<span class="label"><span class="rat-tag ${tagClass}">${escapeAttr(p.label || '—')}</span>${escapeAttr(f.filename)}</span>` +
            `<span class="value">有效 ${(f.stats && f.stats.valid) || 0}</span></div>`;
        } else {
          html += `<div class="result-item error">` +
            `<span class="label">${escapeAttr(f.filename)}</span>` +
            `<span class="value">${escapeAttr(f.error || '失败')}</span></div>`;
        }
      });
      html += '<div style="border-top:1px solid #3a3a3a;margin:8px 0;"></div>';
    }

    html += `<div class="result-item success"><span class="label">合计有效</span><span class="value">${r.stats.valid} 条</span></div>`;
    if (r.stats.invalid > 0) {
      html += `<div class="result-item warn"><span class="label">异常记录</span><span class="value">${r.stats.invalid} 条</span></div>`;
    }
    html += `<div class="result-item"><span class="label">4G LTE</span><span class="value">${r.stats.rat_counts?.LTE || 0} 条</span></div>`;
    html += `<div class="result-item"><span class="label">5G NR</span><span class="value">${r.stats.rat_counts?.NR || 0} 条</span></div>`;
    if (append && r.total_after != null) {
      html += `<div class="result-item success"><span class="label">当前总计</span><span class="value">${r.total_after} 条</span></div>`;
    } else if (r.cells_count != null) {
      html += `<div class="result-item success"><span class="label">入库小区</span><span class="value">${r.cells_count} 条</span></div>`;
    }
    panel.innerHTML = html;
  }

  async function loadSample() {
    log('加载示例数据...', 'info');
    try {
      const blob = await API.downloadSample();
      const file = new File([blob], 'sample_cells.xlsx');
      currentFiles = [file];
      renderFileList();
      doUpload();
    } catch (e) {
      log('加载示例失败: ' + e.message, 'error');
    }
  }

  async function clearDatabase() {
    if (!confirm('确定要清空数据库吗?\n所有工参和规划结果将被永久删除, 无法恢复。')) return;
    log('清空数据库...', 'info');
    try {
      await API.clearDb();
      currentStats = {};
      updateStats();
      loadCells();
      log('数据库已清空', 'success');
    } catch (e) {
      log('清空失败: ' + e.message, 'error');
    }
  }

  // ========== 统计 ==========
  async function loadStats() {
    try {
      const r = await API.getCells();
      currentStats = r.stats || {};
      currentCells = r.cells || [];
      updateStats();
      renderCellsTable();
    } catch (e) {
      log('加载统计失败: ' + e.message, 'error');
    }
  }

  function updateStats() {
    const ratCounts = currentStats.rat_counts || {};
    document.getElementById('stat-total').textContent = currentCells.length;
    document.getElementById('stat-lte').textContent = ratCounts.LTE || 0;
    document.getElementById('stat-nr').textContent = ratCounts.NR || 0;
    document.getElementById('stat-conflict').textContent = currentStats.conflict_count || 0;
    // 网管同步统计
    let synced = 0;
    for (const c of currentCells) {
      if (c.pci_synced_at) synced += 1;
    }
    document.getElementById('stat-synced').textContent = synced;
    document.getElementById('stat-unsynced').textContent = currentCells.length - synced;
    document.getElementById('cells-count').textContent = currentCells.length;
  }

  // ========== 标签页 ==========
  function initTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const tabName = tab.dataset.tab;
        switchTab(tabName);
      });
    });
  }

  function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    const tab = document.querySelector(`.tab[data-tab="${tabName}"]`);
    const content = document.getElementById(`tab-${tabName}`);
    if (tab) tab.classList.add('active');
    if (content) content.classList.add('active');
  }

  // ========== 小区列表 ==========
  async function loadCells() {
    try {
      const r = await API.getCells();
      currentCells = r.cells || [];
      currentStats = { ...currentStats, ...(r.stats || {}) };
      updateStats();
      currentPage = 1;
      renderCellsTable();
    } catch (e) {
      log('加载小区列表失败: ' + e.message, 'error');
    }
  }

  function getFilteredCells() {
    const keyword = (document.getElementById('cells-search').value || '').toLowerCase();
    const rat = document.getElementById('filter-rat').value;
    const syncFilter = document.getElementById('filter-sync').value;

    return currentCells.filter(c => {
      if (rat && c.rat !== rat) return false;
      if (syncFilter === 'synced' && !c.pci_synced_at) return false;
      if (syncFilter === 'unsynced' && c.pci_synced_at) return false;
      if (keyword) {
        const text = `${c.name || ''} ${c.ecgi || ''} ${c.site_name || ''}`.toLowerCase();
        if (!text.includes(keyword)) return false;
      }
      return true;
    });
  }

  function renderCellsTable() {
    const container = document.getElementById('cells-table-container');
    const filtered = getFilteredCells();

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">📡</div>
          <div class="text">${currentCells.length === 0 ? '暂无工参数据，请先导入' : '没有匹配的小区'}</div>
        </div>
      `;
      document.getElementById('cells-pagination').innerHTML = '';
      return;
    }

    const totalPages = Math.ceil(filtered.length / pageSize);
    const start = (currentPage - 1) * pageSize;
    const pageData = filtered.slice(start, start + pageSize);

    const columns = [
      { key: 'name', label: '小区名' },
      { key: 'rat', label: '制式' },
      { key: 'freq_band', label: '频段' },
      { key: 'manufacturer', label: '厂家' },
      { key: 'oms_name', label: '归属网管' },
      { key: 'site_name', label: '站点' },
      { key: 'phy_name', label: '物理站' },
      { key: 'ant_name', label: '天线' },
      { key: 'site_type', label: '站型' },
      { key: 'pci', label: 'PCI', syncedKey: true },
      { key: 'new_pci', label: '新PCI' },
      { key: 'sync_status', label: '网管同步', isHtml: true },
      { key: 'azimuth', label: '方位角' },
      { key: 'beamwidth', label: '波瓣' },
      { key: 'bandwidth', label: '带宽' },
      { key: 'lon', label: '经度' },
      { key: 'lat', label: '纬度' },
      { key: 'tac', label: 'TAC', syncedKey: true },
      { key: 'earfcn', label: '频点', syncedKey: true },
    ];

    let html = '<table class="data-table"><thead><tr>';
    html += '<th style="width:50px;">#</th>';
    columns.forEach(col => {
      html += `<th>${col.label}</th>`;
    });
    html += '<th class="col-actions" style="width:100px;">操作</th>';
    html += '</tr></thead><tbody>';

    pageData.forEach((row, i) => {
      const synced = !!row.pci_synced_at;
      const rowClass = synced ? 'synced-row' : '';
      html += `<tr class="${rowClass}" data-ecgi="${escapeAttr(row.ecgi || '')}">`;
      html += `<td>${start + i + 1}</td>`;
      columns.forEach(col => {
        if (col.key === 'sync_status') {
          if (synced) {
            const ts = formatShortDate(row.pci_synced_at);
            html += `<td><span class="sync-badge synced" title="${escapeAttr(row.pci_synced_at)}">已同步 ${ts}</span></td>`;
          } else {
            html += `<td><span class="sync-badge unsynced">未同步</span></td>`;
          }
          return;
        }
        const val = row[col.key];
        let display = val != null ? val : '';
        if (col.key === 'new_pci' && val != null && val !== row.pci) {
          display = `<span style="color:#67c23a; font-weight:600;">${val}</span>`;
        }
        // 网管同步过的 PCI / TAC / earfcn 用绿色加粗标记
        if (col.syncedKey && synced && val != null && val !== '') {
          display = `<span class="field-synced" title="已通过网管同步, 时间: ${escapeAttr(row.pci_synced_at)}">${val}</span>`;
        }
        if (col.key === 'rat') {
          const color = val === 'LTE' ? '#4fc3f7' : val === 'NR' ? '#a78bfa' : '#ccc';
          display = `<span style="color:${color};">${val || ''}</span>`;
        }
        html += `<td title="${val != null ? val : ''}">${display}</td>`;
      });
      html += `<td class="col-actions">` +
        `<button type="button" class="link-btn" data-action="edit">编辑</button>` +
        `<button type="button" class="link-btn danger" data-action="delete">删除</button>` +
        `</td>`;
      html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;

    container.onclick = (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const tr = btn.closest('tr');
      const ecgi = tr && tr.dataset.ecgi;
      if (!ecgi) return;
      const row = currentCells.find((c) => c.ecgi === ecgi);
      if (!row) return;
      if (btn.dataset.action === 'edit') {
        openCellModal('edit', row);
      } else if (btn.dataset.action === 'delete') {
        deleteCellRow(row);
      }
    };

    renderPagination(filtered.length, totalPages);
  }

  function renderPagination(total, totalPages) {
    const pagination = document.getElementById('cells-pagination');

    if (totalPages <= 1) {
      pagination.innerHTML = '';
      return;
    }

    let html = '';
    html += `<button ${currentPage === 1 ? 'disabled' : ''} onclick="goToPage(${currentPage - 1})">上一页</button>`;

    const maxShow = 7;
    let start = Math.max(1, currentPage - Math.floor(maxShow / 2));
    let end = Math.min(totalPages, start + maxShow - 1);
    if (end - start + 1 < maxShow) start = Math.max(1, end - maxShow + 1);

    if (start > 1) {
      html += `<button onclick="goToPage(1)">1</button>`;
      if (start > 2) html += `<span style="color:#666; padding:0 4px;">...</span>`;
    }

    for (let i = start; i <= end; i++) {
      html += `<button class="${i === currentPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
    }

    if (end < totalPages) {
      if (end < totalPages - 1) html += `<span style="color:#666; padding:0 4px;">...</span>`;
      html += `<button onclick="goToPage(${totalPages})">${totalPages}</button>`;
    }

    html += `<button ${currentPage === totalPages ? 'disabled' : ''} onclick="goToPage(${currentPage + 1})">下一页</button>`;
    html += `<span class="page-info">共 ${total} 条 / ${totalPages} 页</span>`;

    pagination.innerHTML = html;
  }

  window.goToPage = function (page) {
    currentPage = page;
    renderCellsTable();
  };

  // ========== 导入历史 ==========
  // 简化：使用操作日志代替

  // ========== 导出 ==========
  function exportFile(type, vendor) {
    const typeNames = {
      workparams: '工参表',
      neighbors: '邻区清单',
      conflicts: '冲突报表',
      summary: '规划总览',
      mml: 'MML脚本',
    };
    log(`导出 ${typeNames[type] || type}...`, 'info');
    API.exportFile(type, vendor).then(({ blob, filename }) => {
      API.downloadBlob(blob, filename);
      log(`已导出: ${filename}`, 'success');
    }).catch(e => {
      log('导出失败: ' + e.message, 'error');
    });
  }

  // ========== 工具函数 ==========
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function log(message, type) {
    type = type || 'info';
    const panel = document.getElementById('log-panel');
    const line = document.createElement('div');
    line.className = 'log-line ' + type;
    const time = new Date().toLocaleTimeString();
    line.textContent = `[${time}] ${message}`;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dateStr;
    }
  }

  function formatShortDate(dateStr) {
    if (!dateStr) return '';
    try {
      // 后端用 datetime.utcnow() 生成时间戳, 没有时区后缀, JS 会当作本地时间解析
      // 这里手动补 'Z' 让浏览器按 UTC 解析, 再用 toLocaleString 转本地时区显示
      let iso = String(dateStr);
      if (!/[Zz]|[+-]\d{2}:?\d{2}$/.test(iso)) iso += 'Z';
      const d = new Date(iso);
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${mm}-${dd} ${hh}:${mi}`;
    } catch (e) {
      return dateStr;
    }
  }

  function escapeAttr(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();

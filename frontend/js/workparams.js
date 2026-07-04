// 工参导入页面逻辑
(function () {
  'use strict';

  let currentFile = null;
  let currentCells = [];
  let currentStats = {};
  let currentPage = 1;
  const pageSize = 50;
  let importHistory = [];

  // 初始化
  document.addEventListener('DOMContentLoaded', function () {
    initFileUpload();
    initTabs();
    loadStats();
    loadCells();
  });

  // ========== 文件上传 ==========
  function initFileUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileRemove = document.getElementById('file-remove');

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
        handleFile(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length) {
        handleFile(e.target.files[0]);
      }
    });

    fileRemove.addEventListener('click', () => {
      currentFile = null;
      fileInput.value = '';
      document.getElementById('file-info').style.display = 'none';
      document.getElementById('import-result').style.display = 'none';
      log('已移除文件', 'info');
    });

    // 导入按钮
    document.getElementById('btn-upload').addEventListener('click', () => {
      if (!currentFile) {
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
  }

  function handleFile(file) {
    if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
      log('请上传 Excel 或 CSV 文件', 'error');
      return;
    }
    currentFile = file;
    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-info').style.display = 'block';
    document.getElementById('btn-upload').textContent = '开始导入';
    log(`已选择文件: ${file.name} (${formatSize(file.size)})`, 'info');
  }

  function doUpload() {
    if (!currentFile) {
      log('请先选择文件', 'warn');
      return;
    }
    const append = document.getElementById('append-mode').checked;
    const btn = document.getElementById('btn-upload');
    btn.classList.add('btn-loading');
    btn.disabled = true;

    log(`开始导入: ${currentFile.name} (模式: ${append ? '追加' : '替换'})`, 'info');

    API.uploadFile(currentFile, { append }).then(r => {
      currentStats = r.stats;
      renderImportResult(r, append);
      updateStats();
      loadCells();
      log(append
        ? `追加完成: 新增/更新 ${r.added} 条, 当前共 ${r.total_after} 小区`
        : `导入完成: 有效${r.stats.valid}条, 异常${r.stats.invalid}条`,
        r.stats.invalid > 0 ? 'warn' : 'success');
      if (r.invalid_rows && r.invalid_rows.length > 0) {
        log(`异常行号: ${r.invalid_rows.map(x => x.row).join(', ')}`, 'warn');
      }
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
    html += `<div class="result-item success"><span class="label">有效记录</span><span class="value">${r.stats.valid} 条</span></div>`;
    if (r.stats.invalid > 0) {
      html += `<div class="result-item warn"><span class="label">异常记录</span><span class="value">${r.stats.invalid} 条</span></div>`;
    }
    html += `<div class="result-item"><span class="label">4G LTE</span><span class="value">${r.stats.rat_counts?.LTE || 0} 条</span></div>`;
    html += `<div class="result-item"><span class="label">5G NR</span><span class="value">${r.stats.rat_counts?.NR || 0} 条</span></div>`;
    if (append) {
      html += `<div class="result-item success"><span class="label">当前总计</span><span class="value">${r.total_after} 条</span></div>`;
    }
    panel.innerHTML = html;
  }

  async function loadSample() {
    log('加载示例数据...', 'info');
    try {
      const blob = await API.downloadSample();
      const file = new File([blob], 'sample_cells.xlsx');
      currentFile = file;
      document.getElementById('file-name').textContent = file.name;
      document.getElementById('file-info').style.display = 'block';
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
    html += '</tr></thead><tbody>';

    pageData.forEach((row, i) => {
      const synced = !!row.pci_synced_at;
      const rowClass = synced ? 'synced-row' : '';
      html += `<tr class="${rowClass}">`;
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
      html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;

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

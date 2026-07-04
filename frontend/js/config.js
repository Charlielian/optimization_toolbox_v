// 网管数据导入页面逻辑
(function () {
  'use strict';

  // 单文件状态: 'pending' | 'parsing' | 'ready' | 'importing' | 'success' | 'error'
  // fileQueue: [{ id, file, status, statusText, error, sheets, parsedAt, importedAt }]
  let fileQueue = [];
  let currentFile = null;     // 当前激活（可配置 sheet）的文件对象
  let currentSheets = [];       // YAML/数据库配置的sheet
  let excelSheets = [];         // 当前文件的Excel sheet解析结果
  let currentTables = [];
  let currentDataTable = '';
  let currentDataPage = 1;
  let currentConfigSheet = '';  // 当前配置的sheet名
  let currentConfigColumns = []; // 当前配置的列
  const pageSize = 50;
  let nextFileId = 1;
  let isImporting = false;     // 批量导入进行中标志
  let selectedFileId = null;   // 当前在 sheet 区显示的文件 ID

  // 初始化
  document.addEventListener('DOMContentLoaded', function () {
    initFileUpload();
    initTabs();
    loadTables();
    loadHistory();
    loadSheetConfigs();
  });

  // ========== 文件上传（多文件队列） ==========
  function initFileUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    // 防止重复绑定
    if (dropZone && dropZone.dataset.bound === '1') return;
    if (dropZone) dropZone.dataset.bound = '1';

    // 兼容：点击整个拖拽区都触发文件选择
    if (dropZone) {
      dropZone.addEventListener('click', function (e) {
        // 防止点中"清空队列"按钮时也打开选择框
        if (e.target.closest('#btn-clear-queue')) return;
        e.preventDefault();
        fileInput.click();
      });
    }

    if (dropZone) {
      dropZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        dropZone.classList.add('drag-over');
      });
      dropZone.addEventListener('dragleave', function (e) {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
      });
      dropZone.addEventListener('drop', function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('drag-over');
        const files = Array.from(e.dataTransfer ? e.dataTransfer.files : []);
        if (files.length) {
          addFiles(files);
        } else {
          log('拖拽内容不是文件', 'warn');
        }
      });
    }

    if (fileInput) {
      fileInput.addEventListener('change', function (e) {
        try {
          const files = Array.from(e.target.files || []);
          if (files.length) {
            addFiles(files);
          } else {
            log('未选择文件', 'warn');
          }
        } catch (err) {
          log('读取文件失败: ' + err.message, 'error');
        }
        // 重置 input 以允许重复选择相同文件
        e.target.value = '';
      });
    }

    const clearBtn = document.getElementById('btn-clear-queue');
    if (clearBtn) {
      clearBtn.addEventListener('click', clearQueue);
    }
  }

  function addFiles(files) {
    let added = 0;
    files.forEach(f => {
      if (!f || !f.name) {
        log('跳过无效文件', 'warn');
        return;
      }
      if (!f.name.match(/\.(xlsx|xls)$/i)) {
        log(`已忽略非 Excel 文件: ${f.name}`, 'warn');
        return;
      }
      const id = nextFileId++;
      fileQueue.push({
        id,
        file: f,
        status: 'pending',
        statusText: '等待解析',
        error: null,
        sheets: [],
      });
      added++;
      // 异步解析每个文件
      parseQueueItem(id);
    });
    if (added > 0) {
      log(`已加入队列: ${added} 个文件`, 'info');
      renderQueue();
      // 若尚未激活任何文件，则自动激活第一个文件
      if (!selectedFileId) {
        const first = fileQueue[fileQueue.length - added]; // 第一个新加入的
        if (first) selectFile(first.id);
      }
    }
  }

  function clearQueue() {
    if (isImporting) {
      log('导入进行中，无法清空队列', 'warn');
      return;
    }
    fileQueue = [];
    selectedFileId = null;
    currentFile = null;
    excelSheets = [];
    renderQueue();
    document.getElementById('sheet-section').style.display = 'none';
    document.getElementById('import-section').style.display = 'none';
    document.getElementById('file-toolbar').style.display = 'none';
    document.getElementById('import-result').style.display = 'none';
    log('已清空文件队列', 'info');
  }

  function removeQueueItem(id) {
    if (isImporting) {
      log('导入进行中，无法移除文件', 'warn');
      return;
    }
    const item = fileQueue.find(f => f.id === id);
    if (!item) return;
    if (item.status === 'importing') return; // 正在导入的不允许移除

    fileQueue = fileQueue.filter(f => f.id !== id);

    if (selectedFileId === id) {
      selectedFileId = null;
      currentFile = null;
      excelSheets = [];
      document.getElementById('sheet-section').style.display = 'none';
      document.getElementById('import-section').style.display = 'none';
      // 自动选中下一个 ready 的文件
      const next = fileQueue.find(f => f.status === 'ready' || f.status === 'pending');
      if (next) selectFile(next.id);
    }
    renderQueue();
  }

  function selectFile(id) {
    const item = fileQueue.find(f => f.id === id);
    if (!item) return;
    selectedFileId = id;
    currentFile = item.file;

    // 显示该文件对应的 sheet 区
    if (item.status === 'ready') {
      excelSheets = item.sheets;
      loadSheetConfigs(); // 重新渲染 sheet 列表
      document.getElementById('sheet-section').style.display = 'block';
      document.getElementById('import-section').style.display = 'block';
      document.getElementById('import-result').style.display = 'none';
    } else if (item.status === 'pending' || item.status === 'parsing') {
      excelSheets = [];
      document.getElementById('sheet-section').style.display = 'block';
      document.getElementById('import-section').style.display = 'none';
      // 清空 sheet 列表
      document.getElementById('sheet-list').innerHTML = `
        <div style="padding: 20px; text-align: center; color: #888; font-size: 12px;">
          ⏳ 正在解析 ${escapeHtml(item.file.name)} ...
        </div>
      `;
    } else {
      // success / error 状态
      document.getElementById('sheet-section').style.display = 'none';
      document.getElementById('import-section').style.display = 'none';
    }

    updateSheetSectionHint();
    renderQueue();
  }

  function updateSheetSectionHint() {
    const hint = document.getElementById('sheet-section-hint');
    if (hint && currentFile) {
      hint.textContent = `当前文件: ${currentFile.name}`;
    }
  }

  function renderQueue() {
    const wrap = document.getElementById('file-queue');
    const toolbar = document.getElementById('file-toolbar');
    if (fileQueue.length === 0) {
      wrap.innerHTML = '';
      toolbar.style.display = 'none';
      return;
    }
    toolbar.style.display = 'flex';

    wrap.innerHTML = fileQueue.map(item => {
      const isActive = item.id === selectedFileId;
      let icon = '📄';
      let statusText = item.statusText || '';
      if (item.status === 'parsing' || item.status === 'importing') {
        icon = '<span class="q-spinner"></span>';
      } else if (item.status === 'success') {
        icon = '✅';
      } else if (item.status === 'error') {
        icon = '❌';
      } else if (item.status === 'ready') {
        icon = '✅';
      }
      const removable = item.status !== 'importing';
      return `
        <div class="file-queue-item ${isActive ? 'active' : ''} ${item.status === 'success' ? 'success' : ''} ${item.status === 'error' ? 'error' : ''}"
             data-id="${item.id}"
             style="cursor: pointer;"
             onclick="window.__configSelectFile(${item.id})">
          <span class="q-icon">${icon}</span>
          <span class="q-name" title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</span>
          <span class="q-status">${escapeHtml(statusText)}</span>
          ${removable ? `<span class="q-remove" onclick="event.stopPropagation(); window.__configRemoveFile(${item.id})">✕</span>` : '<span style="width:16px;"></span>'}
        </div>
      `;
    }).join('');
  }

  window.__configSelectFile = function (id) { selectFile(id); };
  window.__configRemoveFile = function (id) { removeQueueItem(id); };

  function parseQueueItem(id) {
    const item = fileQueue.find(f => f.id === id);
    if (!item) return;
    item.status = 'parsing';
    item.statusText = '解析中...';
    renderQueue();

    // 额外视觉反馈：滚动到队列区
    const qEl = document.getElementById('file-queue');
    if (qEl && qEl.scrollIntoView) {
      try { qEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
    }
    log(`开始解析: ${item.file.name}`, 'info');

    API.configParseExcel(item.file).then(res => {
      if (res.success) {
        item.status = 'ready';
        item.statusText = `${res.sheets.length} 个 Sheet`;
        item.sheets = res.sheets;
        // 如果用户没有选中任何文件，自动选中第一个 ready 的
        if (!selectedFileId) selectFile(item.id);
        // 如果当前正在查看此文件，刷新 sheet 区
        else if (selectedFileId === item.id) {
          excelSheets = item.sheets;
          loadSheetConfigs();
        }
        log(`已解析: ${item.file.name} (${res.sheets.length} 个 Sheet)`, 'success');
      } else {
        item.status = 'error';
        item.statusText = '解析失败';
        item.error = res.error || '未知错误';
        log(`解析失败: ${item.file.name} - ${item.error}`, 'error');
      }
    }).catch(err => {
      item.status = 'error';
      item.statusText = '解析失败';
      item.error = err.message;
      log(`解析失败: ${item.file.name} - ${err.message}`, 'error');
    }).finally(() => {
      renderQueue();
    });
  }

  // ========== Sheet 选择 ==========
  function loadSheetConfigs() {
    return API.configGetSheetConfigs().then(res => {
      if (res.success) {
        currentSheets = res.configs;
        // 如果有Excel解析结果，合并展示
        if (excelSheets.length > 0) {
          renderSheetMerged();
        } else {
          renderSheetList(res.configs);
        }
      }
    }).catch(err => {
      log('加载配置失败: ' + err.message, 'error');
    });
  }

  function renderSheetMerged() {
    const list = document.getElementById('sheet-list');
    list.innerHTML = '';

    // 以Excel中的sheet为主，结合配置
    excelSheets.forEach(sheet => {
      const cfg = currentSheets.find(c => c.name === sheet.name);
      const isEnabled = cfg ? cfg.enabled : sheet.enabled;
      const colCount = cfg ? cfg.yaml_column_count : sheet.columns.length;

      const item = document.createElement('div');
      item.className = 'sheet-item';
      item.innerHTML = `
        <input type="checkbox" id="sheet-${sheet.name}" value="${sheet.name}" ${isEnabled ? 'checked' : ''} />
        <label for="sheet-${sheet.name}" class="sheet-name">${sheet.name}</label>
        <span class="sheet-count">${sheet.row_count}行 / ${colCount}列</span>
      `;
      list.appendChild(item);
    });

    // 绑定全选/全不选
    document.getElementById('btn-select-all').onclick = () => {
      list.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    };
    document.getElementById('btn-select-none').onclick = () => {
      list.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    };
  }

  function renderSheetList(configs) {
    const list = document.getElementById('sheet-list');
    list.innerHTML = '';

    configs.forEach(cfg => {
      const item = document.createElement('div');
      item.className = 'sheet-item';
      item.innerHTML = `
        <input type="checkbox" id="sheet-${cfg.name}" value="${cfg.name}" ${cfg.enabled ? 'checked' : ''} />
        <label for="sheet-${cfg.name}" class="sheet-name">${cfg.name}</label>
        <span class="sheet-count">${cfg.yaml_column_count}列</span>
      `;
      list.appendChild(item);
    });

    document.getElementById('btn-select-all').onclick = () => {
      list.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    };
    document.getElementById('btn-select-none').onclick = () => {
      list.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    };
  }

  function getSelectedSheets() {
    const list = document.getElementById('sheet-list');
    const checked = list.querySelectorAll('input[type="checkbox"]:checked');
    return Array.from(checked).map(cb => cb.value);
  }

  // ========== 导入 ==========
  document.getElementById('btn-import').addEventListener('click', function () {
    if (isImporting) {
      log('正在导入中，请稍候', 'warn');
      return;
    }
    // 收集所有 ready 状态的文件，对每个文件都用其对应的勾选 sheet 进行导入
    const readyFiles = fileQueue.filter(f => f.status === 'ready');
    if (readyFiles.length === 0) {
      log('没有可导入的文件，请先上传并等待解析完成', 'warn');
      return;
    }

    const sheets = getSelectedSheets();
    if (sheets.length === 0) {
      log('请至少选择一个 Sheet', 'warn');
      return;
    }

    // 按队列顺序依次导入每个 ready 文件
    runQueueImport(readyFiles, sheets);
  });

  async function runQueueImport(files, sheets) {
    isImporting = true;
    const btn = document.getElementById('btn-import');
    btn.classList.add('btn-loading');
    btn.disabled = true;
    btn.textContent = `导入中 (0/${files.length})`;

    // 显示批量结果区，初始化进度条 UI
    const panel = document.getElementById('result-panel');
    const resultDiv = document.getElementById('import-result');
    resultDiv.style.display = 'block';
    panel.innerHTML = `
      <div style="font-weight:600; color:#e0e0e0; margin-bottom:8px;">批量导入进度</div>
      <div class="progress-group">
        <div class="progress-row" id="prog-overall">
          <div class="progress-header">
            <span class="p-name">总体进度</span>
            <span class="p-pct">0%</span>
          </div>
          <div class="progress-bar"><div class="p-fill"></div></div>
          <div class="progress-detail" id="prog-overall-detail">等待开始…</div>
        </div>
        <div class="progress-row" id="prog-file" style="display:none;">
          <div class="progress-header">
            <span class="p-name" id="prog-file-name">当前文件</span>
            <span class="p-pct" id="prog-file-pct">0%</span>
          </div>
          <div class="progress-bar"><div class="p-fill" id="prog-file-fill"></div></div>
          <div class="progress-detail" id="prog-file-detail">—</div>
        </div>
        <div class="progress-row" id="prog-data" style="display:none;">
          <div class="progress-header">
            <span class="p-name" id="prog-data-name">数据写入</span>
            <span class="p-pct" id="prog-data-pct">0%</span>
          </div>
          <div class="progress-bar data"><div class="p-fill" id="prog-data-fill"></div></div>
          <div class="progress-detail" id="prog-data-detail">—</div>
        </div>
      </div>
      <div id="batch-log" style="margin-top:8px;"></div>
    `;

    // 缓存进度条元素
    const overallPct = panel.querySelector('#prog-overall .p-pct');
    const overallFill = panel.querySelector('#prog-overall .p-fill');
    const overallDetail = panel.querySelector('#prog-overall-detail');
    const fileRow = panel.querySelector('#prog-file');
    const fileName = panel.querySelector('#prog-file-name');
    const filePct = panel.querySelector('#prog-file-pct');
    const fileFill = panel.querySelector('#prog-file-fill');
    const fileDetail = panel.querySelector('#prog-file-detail');
    const dataRow = panel.querySelector('#prog-data');
    const dataName = panel.querySelector('#prog-data-name');
    const dataPct = panel.querySelector('#prog-data-pct');
    const dataFill = panel.querySelector('#prog-data-fill');
    const dataDetail = panel.querySelector('#prog-data-detail');

    function setProgress(pct) {
      const p = Math.max(0, Math.min(100, pct));
      overallPct.textContent = `${p.toFixed(1)}%`;
      overallFill.style.width = `${p}%`;
    }
    function setFileProgress(pct, name, detail) {
      fileRow.style.display = 'block';
      const p = Math.max(0, Math.min(100, pct));
      filePct.textContent = `${p.toFixed(1)}%`;
      fileFill.style.width = `${p}%`;
      fileName.textContent = name || '当前文件';
      fileDetail.textContent = detail || '';
    }
    function setDataProgress(pct, name, detail) {
      dataRow.style.display = 'block';
      const p = Math.max(0, Math.min(100, pct));
      dataPct.textContent = `${p.toFixed(1)}%`;
      dataFill.style.width = `${p}%`;
      dataName.textContent = name || '数据写入';
      dataDetail.textContent = detail || '';
    }
    function hideFileProgress() { fileRow.style.display = 'none'; }
    function hideDataProgress() { dataRow.style.display = 'none'; }

    let successCount = 0;
    let failCount = 0;

    // 总体进度 = 100% * (i + 当前文件内部进度) / 文件总数
    // 内部进度: 后端发来的文件级 pct (0-100)
    const totalFiles = files.length;
    let lastFilePct = 0;
    let currentFileIndex = 0;
    function overallFromFile() {
      const base = (currentFileIndex / totalFiles) * 100;
      const cur = (lastFilePct / 100) * (100 / totalFiles);
      setProgress(base + cur);
    }
    setProgress(0);

    for (let i = 0; i < files.length; i++) {
      currentFileIndex = i;
      lastFilePct = 0;
      const item = files[i];
      item.status = 'importing';
      item.statusText = `导入中 (${i + 1}/${totalFiles})`;
      btn.textContent = `导入中 (${i + 1}/${totalFiles})`;
      renderQueue();
      appendBatchLog(`▶ ${item.file.name} 开始导入...`);

      // 重置文件级和数据级进度
      setFileProgress(0, item.file.name, '准备中...');
      setDataProgress(0, '数据写入', '—');
      hideDataProgress();

      try {
        const res = await API.configUploadStream(item.file, sheets.join(','), (payload) => {
          // payload = { pct, stage, sheet, sheet_idx, sheet_total, row, row_total, status, ... }
          const pct = payload.pct || 0;
          lastFilePct = pct;
          overallFromFile();

          if (payload.stage === 'parsing') {
            const status = payload.status || (payload.sheet
              ? `解析 sheet: ${payload.sheet} (${payload.sheet_idx + 1}/${payload.sheet_total})`
              : '解析中...');
            setFileProgress(pct, item.file.name, status);
          } else if (payload.stage === 'sheet_parsed') {
            const rows = payload.rows || 0;
            const cols = payload.columns || 0;
            setFileProgress(pct, item.file.name,
              `✓ ${payload.sheet} 解析完成 (${rows.toLocaleString()} 行 / ${cols} 列)`);
            setDataProgress(0, `写入 ${payload.sheet}`, `0 / ${rows.toLocaleString()} 行`);
          } else if (payload.stage === 'saving') {
            const row = payload.row || 0;
            const rowTotal = payload.row_total || 0;
            const rowPct = rowTotal > 0 ? (row / rowTotal) * 100 : 0;
            const sheetName = payload.sheet || '';
            const si = (payload.sheet_idx || 0) + 1;
            const st = payload.sheet_total || '?';
            setFileProgress(pct, item.file.name, `写入 ${sheetName} (${si}/${st})`);
            setDataProgress(rowPct, `写入 ${sheetName}`,
              `${row.toLocaleString()} / ${rowTotal.toLocaleString()} 行`);
          } else if (payload.stage === 'sheet_done') {
            const rc = payload.row_count || 0;
            setFileProgress(pct, item.file.name,
              `✓ ${payload.sheet} 写入完成 (${rc.toLocaleString()} 行)`);
            hideDataProgress();
          } else if (payload.stage === 'file_done') {
            setFileProgress(100, item.file.name, '✓ 完成');
            hideDataProgress();
          } else if (payload.stage === 'warn') {
            appendBatchLog(`  ⚠ ${item.file.name} · ${payload.sheet || ''}: ${payload.message}`);
          } else if (payload.stage === 'error') {
            appendBatchLog(`  ✖ ${item.file.name}: ${payload.message}`, 'error');
            setFileProgress(pct, item.file.name, `✖ 错误: ${payload.message}`);
            const bar = panel.querySelector('#prog-file .progress-bar');
            if (bar) bar.classList.add('error');
          }
        });

        item.status = 'success';
        const sum = (res && res.import_stats && res.import_stats.sheets)
          ? res.import_stats.sheets.reduce((s, x) => s + (x.row_count || 0), 0)
          : 0;
        const sheetsCount = (res && res.import_stats && res.import_stats.sheets)
          ? res.import_stats.sheets.length : 0;
        item.statusText = `已导入 (${i + 1}/${totalFiles})`;
        item.importedRows = sum;
        appendBatchLog(`✅ ${item.file.name} 完成 (${sheetsCount} 个 Sheet, 共 ${sum.toLocaleString()} 行)`, 'success');
        successCount++;
        lastFilePct = 100;
        overallFromFile();
      } catch (err) {
        item.status = 'error';
        item.statusText = `导入失败: ${err.message}`;
        appendBatchLog(`❌ ${item.file.name} 失败: ${err.message}`, 'error');
        setFileProgress(lastFilePct, item.file.name, `✖ 失败: ${err.message}`);
        failCount++;
      }
      renderQueue();
    }

    // 完成
    isImporting = false;
    btn.classList.remove('btn-loading');
    btn.disabled = false;
    btn.textContent = '导入选中的Sheet';
    setProgress(100);

    const summary = `批量导入完成：成功 ${successCount} 个，失败 ${failCount} 个`;
    appendBatchLog('———  ' + summary, failCount === 0 ? 'success' : 'warn');
    overallDetail.textContent = summary;
    log(summary, failCount === 0 ? 'success' : 'warn');
    loadTables();
    loadHistory();
  }

  function appendBatchLog(msg, level) {
    const container = document.getElementById('batch-log');
    if (!container) return;
    const color = level === 'success' ? '#67c23a'
      : level === 'error' ? '#f56c6c'
      : level === 'warn' ? '#e6a23c'
      : '#aaa';
    const div = document.createElement('div');
    div.style.cssText = `font-size:11px; padding:2px 0; color:${color}; font-family: 'SF Mono', Consolas, monospace; word-break: break-all;`;
    div.textContent = msg;
    container.appendChild(div);
    // 自动滚动到最新
    container.scrollTop = container.scrollHeight;
  }

  function appendBatchProgress(name, msg, level) {
    // 兼容旧调用：转写到 batch-log
    appendBatchLog(`${name}: ${msg}`, level);
  }

  function renderImportResult(res) {
    // 单文件旧版入口（保留，外部直接调用兼容）
    const panel = document.getElementById('result-panel');
    const resultDiv = document.getElementById('import-result');
    resultDiv.style.display = 'block';

    let html = '<div style="font-weight:600; color:#e0e0e0; margin-bottom:8px;">导入结果</div>';
    if (res.import_stats && res.import_stats.sheets) {
      res.import_stats.sheets.forEach(s => {
        html += `
          <div class="result-item success">
            <span class="label">${s.name}</span>
            <span class="value">${s.row_count} 行 / ${s.columns} 列</span>
          </div>
        `;
      });
    }
    if (res.import_stats && res.import_stats.errors && res.import_stats.errors.length) {
      res.import_stats.errors.forEach(e => {
        html += `
          <div class="result-item error">
            <span class="label">错误</span>
            <span class="value">${e}</span>
          </div>
        `;
      });
    }
    if (res.import_stats) {
      html += `
        <div class="result-item" style="margin-top:8px; padding-top:8px; border-top:1px solid #3a3a3a;">
          <span class="label">总计</span>
          <span class="value">${res.import_stats.total_rows} 行</span>
        </div>
      `;
    }
    panel.innerHTML = html;
  }

  // ========== 标签页切换 ==========
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

  // ========== 已导入表 ==========
  function loadTables() {
    API.configGetTables().then(res => {
      if (res.success) {
        currentTables = res.tables;
        renderTables(res.tables);
        document.getElementById('tables-count').textContent = res.tables.length;
      }
    }).catch(err => {
      log('加载表列表失败: ' + err.message, 'error');
    });
  }

  function renderTables(tables) {
    const list = document.getElementById('tables-list');
    if (!tables || tables.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="icon">📊</div>
          <div class="text">暂无已导入的数据表</div>
        </div>
      `;
      return;
    }

    const keyword = (document.getElementById('tables-search').value || '').toLowerCase();
    const filtered = tables.filter(t =>
      t.table_name.toLowerCase().includes(keyword) ||
      t.display_name.toLowerCase().includes(keyword)
    );

    list.innerHTML = filtered.map(t => `
      <div class="table-card">
        <div class="table-icon">📋</div>
        <div class="table-info">
          <div class="table-name">${t.display_name}</div>
          <div class="table-meta">
            <span>${t.row_count || 0} 行</span>
            <span>${t.column_count || 0} 列</span>
            ${t.last_imported ? `<span>${formatDate(t.last_imported)}</span>` : ''}
          </div>
        </div>
        <div class="table-actions">
          <button class="btn btn-small" onclick="viewTableData('${t.display_name}')">查看</button>
          <button class="btn btn-small btn-danger" onclick="deleteTable('${t.display_name}')">删除</button>
        </div>
      </div>
    `).join('');
  }

  document.getElementById('btn-refresh-tables').addEventListener('click', loadTables);
  document.getElementById('tables-search').addEventListener('input', () => {
    renderTables(currentTables);
  });

  window.deleteTable = function (tableName) {
    if (!confirm(`确定删除表 ${tableName} 吗？此操作不可恢复。`)) return;
    API.configDeleteTable(tableName).then(() => {
      log(`已删除表: ${tableName}`, 'success');
      loadTables();
    }).catch(err => {
      log('删除失败: ' + err.message, 'error');
    });
  };

  // ========== 数据查看 ==========
  window.viewTableData = function (tableName) {
    currentDataTable = tableName;
    currentDataPage = 1;
    document.getElementById('data-table-title').textContent = tableName;

    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('tab-data').classList.add('active');

    loadDataTable();
  };

  function loadDataTable() {
    const keyword = document.getElementById('data-search').value || '';
    API.configGetData(currentDataTable, currentDataPage, pageSize, keyword).then(res => {
      if (res.success) {
        // 后端 /api/config/data 返回 {rows, total, columns, ...}（参考 db.py:488-496）
        // 兼容旧字段 data，避免出现"表里有数据但页面显示暂无数据"
        const rows = res.rows || res.data || [];
        renderDataTable(rows, res.total, res.columns);
      }
    }).catch(err => {
      log('加载数据失败: ' + err.message, 'error');
    });
  }

  function renderDataTable(data, total, columns) {
    const container = document.getElementById('data-table-container');
    if (!data || data.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">📭</div>
          <div class="text">暂无数据</div>
        </div>
      `;
      return;
    }

    let html = '<table class="data-table"><thead><tr>';
    html += '<th style="width:50px;">#</th>';
    columns.forEach(col => {
      html += `<th>${col}</th>`;
    });
    html += '</tr></thead><tbody>';

    const startIdx = (currentDataPage - 1) * pageSize;
    data.forEach((row, i) => {
      html += `<tr>`;
      html += `<td>${startIdx + i + 1}</td>`;
      columns.forEach(col => {
        const val = row[col];
        html += `<td title="${val != null ? val : ''}">${val != null ? val : ''}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;

    renderPagination(total);
  }

  function renderPagination(total) {
    const totalPages = Math.ceil(total / pageSize);
    const pagination = document.getElementById('data-pagination');

    if (totalPages <= 1) {
      pagination.innerHTML = '';
      return;
    }

    let html = '';
    html += `<button ${currentDataPage === 1 ? 'disabled' : ''} onclick="goToPage(${currentDataPage - 1})">上一页</button>`;

    const maxShow = 5;
    let start = Math.max(1, currentDataPage - Math.floor(maxShow / 2));
    let end = Math.min(totalPages, start + maxShow - 1);
    if (end - start + 1 < maxShow) start = Math.max(1, end - maxShow + 1);

    for (let i = start; i <= end; i++) {
      html += `<button class="${i === currentDataPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
    }

    html += `<button ${currentDataPage === totalPages ? 'disabled' : ''} onclick="goToPage(${currentDataPage + 1})">下一页</button>`;
    html += `<span class="page-info">共 ${total} 条 / ${totalPages} 页</span>`;

    pagination.innerHTML = html;
  }

  window.goToPage = function (page) {
    currentDataPage = page;
    loadDataTable();
  };

  document.getElementById('data-search').addEventListener('input', () => {
    currentDataPage = 1;
    loadDataTable();
  });

  // ========== 导入历史 ==========
  function loadHistory() {
    API.configGetHistory(50).then(res => {
      if (res.success) {
        renderHistory(res.history);
        document.getElementById('history-count').textContent = res.history.length;
      }
    }).catch(err => {
      log('加载历史失败: ' + err.message, 'error');
    });
  }

  function renderHistory(history) {
    const list = document.getElementById('history-list');
    if (!history || history.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="icon">📜</div>
          <div class="text">暂无导入历史</div>
        </div>
      `;
      return;
    }

    list.innerHTML = `<table class="data-table"><thead><tr>
      <th>时间</th>
      <th>文件名</th>
      <th>Sheet名称</th>
      <th>行数</th>
      <th>列数</th>
    </tr></thead><tbody>` + history.map(h => `
      <tr>
        <td>${formatDate(h.imported_at)}</td>
        <td>${h.file_name || ''}</td>
        <td>${h.table_name || ''}</td>
        <td>${h.row_count || 0}</td>
        <td>${h.column_count || 0}</td>
      </tr>
    `).join('') + '</tbody></table>';
  }

  document.getElementById('btn-refresh-history').addEventListener('click', loadHistory);

  // ========== 列配置弹窗 ==========
  let configExcelSheets = [];  // 列配置弹窗中解析的Excel sheet
  let configCurrentFile = null; // 列配置弹窗中的文件
  let configSourceMode = 'excel'; // 'excel' | 'yaml'

  document.getElementById('btn-sheet-config').addEventListener('click', function () {
    openConfigModal();
  });

  function openConfigModal() {
    document.getElementById('config-modal').style.display = 'flex';
    configSourceMode = 'excel';
    updateConfigSourceButtons();

    // 初始化
    configExcelSheets = [];
    configCurrentFile = null;
    currentConfigSheet = '';
    currentConfigColumns = [];
    document.getElementById('config-description').value = '';
    document.getElementById('config-enabled').checked = true;

    // 重置文件上传区
    document.getElementById('config-file-info').style.display = 'none';
    document.getElementById('config-file-name').textContent = '';

    // 初始化Sheet下拉
    updateSheetSelectOptions();

    // 重置列配置区域
    document.getElementById('config-columns').innerHTML = `
      <div style="color:#888; font-size:12px; text-align:center; padding:30px;">
        请先上传 Excel 文件或切换到 YAML 模式，然后选择 Sheet
      </div>
    `;

    // 初始化文件上传
    initConfigFileUpload();
  }

  function updateConfigSourceButtons() {
    const btnYaml = document.getElementById('btn-config-source-yaml');
    const btnExcel = document.getElementById('btn-config-source-excel');
    const excelSection = document.getElementById('config-excel-section');

    if (configSourceMode === 'excel') {
      btnExcel.classList.add('btn-primary');
      btnYaml.classList.remove('btn-primary');
      excelSection.style.display = 'block';
    } else {
      btnYaml.classList.add('btn-primary');
      btnExcel.classList.remove('btn-primary');
      excelSection.style.display = 'none';
    }
  }

  document.getElementById('btn-config-source-excel').addEventListener('click', function () {
    configSourceMode = 'excel';
    updateConfigSourceButtons();
    updateSheetSelectOptions();
  });

  document.getElementById('btn-config-source-yaml').addEventListener('click', function () {
    configSourceMode = 'yaml';
    updateConfigSourceButtons();
    updateSheetSelectOptions();
  });

  function updateSheetSelectOptions() {
    const select = document.getElementById('config-sheet-select');
    select.innerHTML = '<option value="">请选择Sheet</option>';

    let options = [];
    if (configSourceMode === 'excel') {
      if (configExcelSheets.length > 0) {
        options = configExcelSheets.map(s => s.name);
      } else {
        select.innerHTML = '<option value="">请先上传Excel文件</option>';
        return;
      }
    } else {
      // YAML模式：使用YAML配置的sheet
      if (currentSheets.length > 0) {
        options = currentSheets.map(s => s.name);
      } else {
        select.innerHTML = '<option value="">暂无YAML配置</option>';
        return;
      }
    }

    options.forEach(name => {
      select.innerHTML += `<option value="${name}">${name}</option>`;
    });
  }

  function initConfigFileUpload() {
    const dropZone = document.getElementById('config-drop-zone');
    const fileInput = document.getElementById('config-file-input');
    const fileRemove = document.getElementById('config-file-remove');

    dropZone.onclick = () => fileInput.click();

    dropZone.ondragover = (e) => {
      e.preventDefault();
      dropZone.classList.add('drag-over');
    };
    dropZone.ondragleave = () => {
      dropZone.classList.remove('drag-over');
    };
    dropZone.ondrop = (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      if (e.dataTransfer.files.length) {
        handleConfigFile(e.dataTransfer.files[0]);
      }
    };

    fileInput.onchange = (e) => {
      if (e.target.files.length) {
        handleConfigFile(e.target.files[0]);
      }
    };

    fileRemove.onclick = () => {
      configCurrentFile = null;
      configExcelSheets = [];
      fileInput.value = '';
      document.getElementById('config-file-info').style.display = 'none';
      updateSheetSelectOptions();
      log('已移除配置文件', 'info');
    };
  }

  function handleConfigFile(file) {
    if (!file.name.match(/\.(xlsx|xls)$/i)) {
      log('请上传 Excel 文件 (.xlsx / .xls)', 'error');
      return;
    }
    configCurrentFile = file;
    document.getElementById('config-file-name').textContent = file.name;
    document.getElementById('config-file-info').style.display = 'block';
    log(`正在解析配置文件: ${file.name}...`, 'info');

    API.configParseExcel(file).then(res => {
      if (res.success) {
        configExcelSheets = res.sheets;
        log(`解析完成，共 ${configExcelSheets.length} 个 Sheet`, 'success');
        updateSheetSelectOptions();
      }
    }).catch(err => {
      log('解析Excel失败: ' + err.message, 'error');
    });
  }

  document.getElementById('btn-add-custom-sheet').addEventListener('click', function () {
    const customName = prompt('请输入Sheet名称:');
    if (customName && customName.trim()) {
      const name = customName.trim();
      const select = document.getElementById('config-sheet-select');
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.insertBefore(option, select.firstChild);
      select.value = name;
      // 触发change事件
      select.dispatchEvent(new Event('change'));
    }
  });

  document.getElementById('config-sheet-select').addEventListener('change', function () {
    const sheetName = this.value;
    if (!sheetName) {
      document.getElementById('config-columns').innerHTML = `
        <div style="color:#888; font-size:12px; text-align:center; padding:30px;">
          请选择 Sheet 以配置列
        </div>
      `;
      currentConfigSheet = '';
      currentConfigColumns = [];
      document.getElementById('config-description').value = '';
      document.getElementById('config-enabled').checked = true;
      return;
    }

    currentConfigSheet = sheetName;
    loadSheetConfigDetail(sheetName);
  });

  function loadSheetConfigDetail(sheetName) {
    // 获取Excel中的sheet信息（如果有）
    let excelSheet = null;
    if (configSourceMode === 'excel') {
      excelSheet = configExcelSheets.find(s => s.name === sheetName);
    } else {
      excelSheet = excelSheets.find(s => s.name === sheetName);
    }

    API.configGetSheetConfig(sheetName).then(res => {
      if (res.success) {
        let columns = res.columns || [];
        let source = res.source || 'yaml';

        // 填充描述和启用状态
        const sheetCfg = currentSheets.find(s => s.name === sheetName);
        if (sheetCfg) {
          document.getElementById('config-description').value = sheetCfg.description || '';
          document.getElementById('config-enabled').checked = sheetCfg.enabled;
        } else {
          document.getElementById('config-description').value = '';
          document.getElementById('config-enabled').checked = true;
        }

        // 如果有Excel解析结果，用Excel的列名补充
        if (excelSheet && excelSheet.columns && excelSheet.columns.length > 0) {
          const existingCols = new Set(columns.map(c => c.column_src));
          let order = columns.length;
          excelSheet.columns.forEach(colName => {
            if (!existingCols.has(colName)) {
              columns.push({
                column_src: colName,
                column_dst: colName.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_|_$/g, ''),
                data_type: 'TEXT',
                is_pk: false,
                is_enabled: true,
                display_order: order++,
              });
            }
          });
          if (!columns.some(c => c.is_enabled)) {
            source = 'excel';
          }
        }

        currentConfigColumns = columns;
        renderConfigColumns(columns, source);
      }
    }).catch(err => {
      // 失败了也用Excel的列名展示
      if (excelSheet && excelSheet.columns && excelSheet.columns.length > 0) {
        const columns = excelSheet.columns.map((col, i) => ({
          column_src: col,
          column_dst: col.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_|_$/g, ''),
          data_type: 'TEXT',
          is_pk: false,
          is_enabled: true,
          display_order: i,
        }));
        currentConfigColumns = columns;
        document.getElementById('config-description').value = '';
        document.getElementById('config-enabled').checked = true;
        renderConfigColumns(columns, 'excel');
      } else {
        currentConfigColumns = [];
        document.getElementById('config-description').value = '';
        document.getElementById('config-enabled').checked = true;
        renderConfigColumns([], 'new');
      }
    });
  }

  function renderConfigColumns(columns, source) {
    const container = document.getElementById('config-columns');
    if (!columns || columns.length === 0) {
      container.innerHTML = `
        <div style="padding:20px;">
          <p style="color:#888; font-size:12px; margin-bottom:10px;">暂无列配置，点击下方按钮添加</p>
          <button class="btn btn-small btn-primary" id="btn-add-column">+ 添加列</button>
        </div>
      `;
      document.getElementById('btn-add-column').onclick = () => addConfigColumn();
      return;
    }

    const sourceText = {
      'database': '数据库配置',
      'yaml': 'YAML 预定义',
      'excel': 'Excel 解析',
      'new': '手动新建',
    };

    let html = `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">`;
    html += `<div style="font-size:11px; color:#888;">
      配置来源: <span style="color:#7c4dff; font-weight:600;">${sourceText[source] || source}</span>
      · 共 <b style="color:#fff;">${columns.length}</b> 列
    </div>`;
    html += `<div style="display:flex; gap:4px;">
      <button class="btn btn-small" id="btn-enable-all">全部启用</button>
      <button class="btn btn-small" id="btn-disable-all">全部禁用</button>
      <button class="btn btn-small btn-primary" id="btn-add-column">+ 添加列</button>
    </div>`;
    html += `</div>`;

    html += '<div style="overflow:auto; max-height:360px; border:1px solid #3a3a3a; border-radius:4px;">';
    html += '<table class="data-table" style="border:none; border-radius:0;"><thead><tr>';
    html += '<th style="width:50px;">启用</th>';
    html += '<th style="min-width:140px;">Excel列名</th>';
    html += '<th style="min-width:140px;">数据库列名</th>';
    html += '<th style="width:90px;">数据类型</th>';
    html += '<th style="width:50px;">主键</th>';
    html += '<th style="width:60px;">操作</th>';
    html += '</tr></thead><tbody>';

    columns.forEach((col, i) => {
      html += `
        <tr data-idx="${i}">
          <td style="text-align:center;">
            <input type="checkbox" class="col-enabled" ${col.is_enabled ? 'checked' : ''} />
          </td>
          <td>
            <input type="text" class="col-src" value="${escapeHtml(col.column_src)}"
              style="width:100%; height:26px; font-size:11px;" />
          </td>
          <td>
            <input type="text" class="col-dst" value="${escapeHtml(col.column_dst)}"
              style="width:100%; height:26px; font-size:11px;" />
          </td>
          <td>
            <select class="col-type" style="width:100%; height:26px; font-size:11px;">
              <option value="TEXT" ${col.data_type === 'TEXT' ? 'selected' : ''}>TEXT</option>
              <option value="INTEGER" ${col.data_type === 'INTEGER' ? 'selected' : ''}>INTEGER</option>
              <option value="REAL" ${col.data_type === 'REAL' ? 'selected' : ''}>REAL</option>
            </select>
          </td>
          <td style="text-align:center;">
            <input type="checkbox" class="col-pk" ${col.is_pk ? 'checked' : ''} />
          </td>
          <td style="text-align:center;">
            <button class="btn btn-small btn-danger" style="height:22px; padding:0 8px; font-size:10px;"
              onclick="removeConfigColumn(${i})">删除</button>
          </td>
        </tr>
      `;
    });
    html += '</tbody></table></div>';
    container.innerHTML = html;

    // 绑定事件
    document.getElementById('btn-enable-all').onclick = () => {
      container.querySelectorAll('.col-enabled').forEach(cb => cb.checked = true);
    };
    document.getElementById('btn-disable-all').onclick = () => {
      container.querySelectorAll('.col-enabled').forEach(cb => cb.checked = false);
    };
    document.getElementById('btn-add-column').onclick = () => addConfigColumn();

    // 自动生成数据库列名：当Excel列名变化时
    container.querySelectorAll('.col-src').forEach((input, i) => {
      input.addEventListener('blur', function () {
        const dstInput = this.closest('tr').querySelector('.col-dst');
        if (dstInput && !dstInput.dataset.manual) {
          const val = this.value.trim();
          if (val) {
            dstInput.value = val.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_|_$/g, '');
          }
        }
      });
    });

    // 标记手动修改的数据库列名
    container.querySelectorAll('.col-dst').forEach(input => {
      input.addEventListener('input', function () {
        this.dataset.manual = 'true';
      });
    });
  }

  function addConfigColumn() {
    currentConfigColumns.push({
      column_src: 'new_column',
      column_dst: 'new_column',
      data_type: 'TEXT',
      is_pk: false,
      is_enabled: true,
      display_order: currentConfigColumns.length,
    });
    renderConfigColumns(currentConfigColumns, 'new');
  }

  window.removeConfigColumn = function (idx) {
    currentConfigColumns.splice(idx, 1);
    renderConfigColumns(currentConfigColumns, 'new');
  };

  function collectConfigColumns() {
    const rows = document.querySelectorAll('#config-columns tbody tr');
    const columns = [];
    rows.forEach((row, i) => {
      columns.push({
        column_src: row.querySelector('.col-src').value,
        column_dst: row.querySelector('.col-dst').value,
        data_type: row.querySelector('.col-type').value,
        is_pk: row.querySelector('.col-pk').checked,
        is_enabled: row.querySelector('.col-enabled').checked,
      });
    });
    return columns;
  }

  document.getElementById('btn-save-config').addEventListener('click', function () {
    if (!currentConfigSheet) {
      log('请选择或输入Sheet名称', 'warn');
      return;
    }

    const columns = collectConfigColumns();
    if (columns.length === 0) {
      log('请至少添加一列', 'warn');
      return;
    }

    const description = document.getElementById('config-description').value;
    const enabled = document.getElementById('config-enabled').checked;

    const btn = document.getElementById('btn-save-config');
    btn.classList.add('btn-loading');
    btn.disabled = true;

    API.configSaveSheetConfig(currentConfigSheet, columns, enabled, description).then(r => {
      log(`配置已保存 (${columns.length} 列)`, 'success');
      if (r.yaml_saved) {
        log('已同步保存到 YAML 配置文件', 'success');
      }
      closeModal('config-modal');
      // 重新加载配置
      loadSheetConfigs();
      // 如果主界面有文件，重新解析
      if (currentFile) {
        parseExcelSheets(currentFile);
      }
    }).catch(err => {
      log('保存失败: ' + err.message, 'error');
    }).finally(() => {
      btn.classList.remove('btn-loading');
      btn.disabled = false;
    });
  });

  document.getElementById('btn-reload-config').addEventListener('click', function () {
    API.configReload().then(() => {
      log('配置已重新加载', 'success');
      loadSheetConfigs();
    }).catch(err => {
      log('刷新配置失败: ' + err.message, 'error');
    });
  });

  window.closeModal = function (modalId) {
    document.getElementById(modalId).style.display = 'none';
  };

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

  function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dateStr;
    }
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();

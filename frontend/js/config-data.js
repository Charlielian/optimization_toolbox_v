// 网管数据查看页面逻辑
(function () {
  'use strict';

  let currentTables = [];
  let currentTable = '';
  let currentData = [];
  let currentColumns = [];
  let currentMeta = [];      // 列元信息: [{name, desc, src, type, ...}]
  let currentTotal = 0;
  let currentPage = 1;
  let pageSize = 50;

  const SYNC_BC = typeof BroadcastChannel !== 'undefined'
    ? new BroadcastChannel('wybb-cells-sync')
    : null;

  // 初始化
  document.addEventListener('DOMContentLoaded', function () {
    loadTables();
    bindEvents();
    refreshWorkparamSyncHint();
  });

  function setSyncStatusBar(mode, html) {
    const bar = document.getElementById('sync-status-bar');
    const text = document.getElementById('sync-status-text');
    const link = document.getElementById('sync-status-link');
    if (!bar || !text) return;
    bar.classList.remove('syncing', 'ok', 'err');
    if (mode === 'idle') {
      bar.classList.remove('visible');
      return;
    }
    bar.classList.add('visible', mode);
    text.innerHTML = html;
    if (link) {
      link.style.display = mode === 'ok' ? '' : 'none';
    }
  }

  async function refreshWorkparamSyncHint() {
    try {
      const r = await API.getCells();
      if (!r.success || !r.cells) return;
      const synced = r.cells.filter(c => c.pci_synced_at).length;
      const total = r.cells.length;
      if (total === 0) {
        setSyncStatusBar('ok', '工参库暂无小区；同步将写入已导入工参中与网管 CGI 匹配的记录。');
        return;
      }
      setSyncStatusBar(
        'ok',
        `工参 <b>${total}</b> 条，其中 <b>${synced}</b> 条已标记网管同步（「工参管理」→ 网管同步列）。`
      );
    } catch (e) {
      /* 忽略：服务未就绪 */
    }
  }

  function bindEvents() {
    // 搜索表
    document.getElementById('table-search').addEventListener('input', renderTableList);

    // 搜索数据
    document.getElementById('data-search').addEventListener('input', () => {
      currentPage = 1;
      loadData();
    });

    // 刷新
    document.getElementById('btn-refresh').addEventListener('click', () => {
      loadTables();
      if (currentTable) loadData();
    });

    // 导出
    document.getElementById('btn-export').addEventListener('click', exportTable);

    // 删除表
    document.getElementById('btn-delete').addEventListener('click', deleteCurrentTable);

    // 同步网管基础数据
    document.getElementById('btn-sync-cells').addEventListener('click', syncCellsFromConfig);

    // 分页
    document.getElementById('page-prev').addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        loadData();
      }
    });
    document.getElementById('page-next').addEventListener('click', () => {
      const totalPages = Math.ceil(currentTotal / pageSize);
      if (currentPage < totalPages) {
        currentPage++;
        loadData();
      }
    });
    document.getElementById('page-size').addEventListener('change', (e) => {
      pageSize = parseInt(e.target.value, 10) || 50;
      currentPage = 1;
      loadData();
    });
  }

  // ========== 表列表 ==========
  async function loadTables() {
    try {
      const r = await API.configGetTables();
      if (r.success) {
        currentTables = r.tables;
        renderTableList();
      }
    } catch (e) {
      console.error('加载表列表失败:', e);
    }
  }

  function renderTableList() {
    const container = document.getElementById('table-list');
    const keyword = (document.getElementById('table-search').value || '').toLowerCase();

    const filtered = currentTables.filter(t =>
      t.display_name.toLowerCase().includes(keyword) ||
      t.table_name.toLowerCase().includes(keyword)
    );

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="empty-state" style="padding: 40px 10px;">
          <div class="icon" style="font-size: 32px;">📊</div>
          <div class="text" style="font-size: 12px;">
            ${currentTables.length === 0 ? '暂无数据表' : '没有匹配的表'}
          </div>
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(t => `
      <div class="table-list-item ${t.display_name === currentTable ? 'active' : ''}"
           data-table="${t.display_name}">
        <div class="table-icon">📋</div>
        <div class="table-info">
          <div class="table-name" title="${t.display_name}">${t.display_name}</div>
          <div class="table-meta">${t.row_count || 0} 行 · ${t.column_count || 0} 列</div>
        </div>
      </div>
    `).join('');

    // 绑定点击事件
    container.querySelectorAll('.table-list-item').forEach(item => {
      item.addEventListener('click', () => {
        const tableName = item.dataset.table;
        selectTable(tableName);
      });
    });
  }

  function selectTable(tableName) {
    currentTable = tableName;
    currentPage = 1;
    renderTableList();
    loadData();

    // 显示工具栏
    document.getElementById('current-table-title').textContent = tableName;
    document.getElementById('data-search').style.display = 'block';
    document.getElementById('btn-refresh').style.display = 'block';
    document.getElementById('btn-export').style.display = 'block';
    document.getElementById('btn-delete').style.display = 'block';
    document.getElementById('table-info-bar').style.display = 'flex';
    document.getElementById('pagination').style.display = 'flex';
  }

  // ========== 数据加载 ==========
  async function loadData() {
    if (!currentTable) return;

    const keyword = document.getElementById('data-search').value || '';
    try {
      const r = await API.configGetData(currentTable, currentPage, pageSize, keyword);
      if (r.success) {
        currentData = r.rows || r.data || [];
        currentColumns = r.columns || [];
        currentMeta = r.column_meta || [];
        currentTotal = r.total || 0;
        renderDataTable();
        updatePagination();
        updateTableInfo();
      }
    } catch (e) {
      console.error('加载数据失败:', e);
      document.getElementById('data-container').innerHTML = `
        <div class="empty-state">
          <div class="icon">❌</div>
          <div class="text">加载数据失败</div>
          <div class="hint">${e.message || '请重试'}</div>
        </div>
      `;
    }
  }

  function renderDataTable() {
    const container = document.getElementById('data-container');

    if (!currentData || currentData.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">📭</div>
          <div class="text">暂无数据</div>
          <div class="hint">该表中没有数据</div>
        </div>
      `;
      return;
    }

    // 根据 name 查找元信息（兼容旧数据：找不到就用 name 自身）
    const metaByName = {};
    currentMeta.forEach(m => { if (m && m.name) metaByName[m.name] = m; });

    let html = '<table class="data-table"><thead><tr>';
    html += '<th rowspan="2" style="width:50px; position:sticky; left:0; background:#1a1a1a; z-index:11;">#</th>';
    currentColumns.forEach(col => {
      const meta = metaByName[col] || {};
      const desc = meta.desc || '';
      const src  = meta.src  || '';
      const title = src ? `${col}\n来源: ${src}` : col;
      html += `<th title="${title}">${col}</th>`;
    });
    html += '</tr><tr>';
    currentColumns.forEach(col => {
      const meta = metaByName[col] || {};
      const desc = meta.desc || '';
      const style = 'font-size:12px;color:#9aa;font-weight:normal;background:#1a1a1a;';
      html += `<th style="${style}">${desc}</th>`;
    });
    html += '</tr></thead><tbody>';

    const startIdx = (currentPage - 1) * pageSize;
    currentData.forEach((row, i) => {
      html += `<tr>`;
      html += `<td style="position:sticky; left:0; background:#2a2a2a; z-index:10;">${startIdx + i + 1}</td>`;
      currentColumns.forEach(col => {
        const val = row[col];
        const display = val != null ? val : '';
        html += `<td title="${display}">${display}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    container.innerHTML = html;
  }

  function updatePagination() {
    const totalPages = Math.ceil(currentTotal / pageSize);
    document.getElementById('page-info').textContent = `第 ${currentPage} 页 / 共 ${totalPages} 页 (${currentTotal} 条)`;
    document.getElementById('page-prev').disabled = currentPage <= 1;
    document.getElementById('page-next').disabled = currentPage >= totalPages;
  }

  function updateTableInfo() {
    const tableInfo = currentTables.find(t => t.display_name === currentTable);
    if (!tableInfo) return;

    document.getElementById('info-rows').textContent = tableInfo.row_count || 0;
    document.getElementById('info-cols').textContent = tableInfo.column_count || 0;
    document.getElementById('info-time').textContent = tableInfo.last_imported
      ? formatDate(tableInfo.last_imported)
      : '-';
  }

  // ========== 操作 ==========
  function exportTable() {
    if (!currentTable) return;
    // 用浏览器原生方式导出 CSV
    if (!currentData || currentData.length === 0) {
      alert('暂无数据可导出');
      return;
    }
    // 简单 CSV 导出
    const csvContent = [
      currentColumns.join(','),
      ...currentData.map(row =>
        currentColumns.map(col => {
          const val = row[col] != null ? String(row[col]) : '';
          return val.includes(',') || val.includes('"')
            ? `"${val.replace(/"/g, '""')}"`
            : val;
        }).join(',')
      )
    ].join('\n');

    const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${currentTable}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function deleteCurrentTable() {
    if (!currentTable) return;
    if (!confirm(`确定删除表 ${currentTable} 吗？\n此操作不可恢复！`)) return;

    API.configDeleteTable(currentTable).then(() => {
      currentTable = '';
      currentData = [];
      currentColumns = [];
      currentTotal = 0;
      loadTables();
      resetView();
    }).catch(e => {
      alert('删除失败: ' + e.message);
    });
  }

  function resetView() {
    document.getElementById('current-table-title').textContent = '请选择一个数据表';
    document.getElementById('data-search').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('btn-export').style.display = 'none';
    document.getElementById('btn-delete').style.display = 'none';
    document.getElementById('table-info-bar').style.display = 'none';
    document.getElementById('pagination').style.display = 'none';
    document.getElementById('data-container').innerHTML = `
      <div class="empty-state">
        <div class="icon">📋</div>
        <div class="text">请从左侧选择一个数据表</div>
        <div class="hint">选择后将显示表中数据</div>
      </div>
    `;
  }

  // ========== 工具函数 ==========
  function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      return d.toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
      });
    } catch (e) {
      return dateStr;
    }
  }

  // ========== 同步网管基础数据 ==========
  function syncCellsFromConfig() {
    if (!confirm(
      '确定要从网管配置表同步基础数据到 cells 表吗?\n\n' +
      '将读取以下表的 cgi 清单并更新 cells 表的 pci/tac/earfcn/freq_band_ind:\n' +
      '  • cfg_CUEUtranCellFDDLTE (earfcn_dl)\n' +
      '  • cfg_CUEUtranCellTDDLTE (earfcn)\n' +
      '  • cfg_EUtranCellFDD      (earfcn_dl)\n\n' +
      '匹配规则: 拼接 cgi = 460-00-{enbid}-{cell_local_id} 与 cells.ecgi 相等的小区'
    )) {
      return;
    }

    const btn = document.getElementById('btn-sync-cells');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.classList.add('btn-sync-running');
    btn.textContent = '同步中…';
    setSyncStatusBar(
      'syncing',
      '<span class="sync-spin"></span> 正在读取网管 cfg 表并更新工参库，请稍候…'
    );

    API.configSyncCells()
      .then(r => {
        btn.disabled = false;
        btn.classList.remove('btn-sync-running');
        btn.textContent = originalText;
        if (!r || !r.success) {
          const msg = (r && r.message) ? r.message : '未知错误';
          setSyncStatusBar('err', `同步失败：${escapeHtml(msg)}`);
          alert('同步失败: ' + msg);
          if (r) {
            showSyncResult({
              success: false,
              message: r.message || '同步失败',
              stats: r.stats || {},
              build_stats: r.build_stats || {},
              missing_tables: r.missing_tables || [],
              earfcn_unmapped_count: r.earfcn_unmapped_count || 0,
              earfcn_unmapped_sample: r.earfcn_unmapped_sample || [],
            });
          }
          return;
        }
        const st = r.stats || {};
        const matched = st.matched_ecgis ?? 0;
        const marked = st.marked_synced ?? matched;
        const syncedAll = r.workparam_synced_count ?? marked;
        const totalAll = r.cells_in_memory ?? '—';
        setSyncStatusBar(
          'ok',
          `同步完成：本次匹配更新 <b>${matched}</b> 条 CGI；工参库共 <b>${totalAll}</b> 条，`
          + `已标记网管同步 <b>${syncedAll}</b> 条。请到「工参管理」查看「网管同步」列。`
        );
        if (SYNC_BC) {
          SYNC_BC.postMessage({ type: 'cells-sync-done', at: Date.now(), stats: st });
        }
        try {
          localStorage.setItem('wybb_cells_sync_at', String(Date.now()));
        } catch (err) { /* ignore */ }
        showSyncResult(r);
      })
      .catch(e => {
        btn.disabled = false;
        btn.classList.remove('btn-sync-running');
        btn.textContent = originalText;
        const msg = e.message || String(e);
        setSyncStatusBar('err', `请求失败：${escapeHtml(msg)}`);
        alert('同步请求失败: ' + msg);
      });
  }

  function showSyncResult(r) {
    // 移除旧 modal
    const old = document.getElementById('sync-result-modal');
    if (old) old.remove();

    const stats = r.stats || {};
    const buildStats = r.build_stats || {};
    const missingTables = r.missing_tables || [];
    const unmapped = r.earfcn_unmapped_sample || [];

    // 渲染每张源表的统计
    const sourceStatsHtml = Object.keys(buildStats).length === 0
      ? '<div class="table-stat" style="color:#888;">(无源表数据)</div>'
      : Object.entries(buildStats).map(([t, n]) =>
          `<div class="table-stat">${escapeHtml(t)}: <b style="color:#4caf50;">${n}</b> 条 cgi</div>`
        ).join('');

    // 未在映射命中的样例
    const unmappedHtml = unmapped.length === 0
      ? '<div class="warn-list"><div class="empty">全部命中 earfcn 映射表</div></div>'
      : `<div class="warn-list">${
          unmapped.map(u => `• ${escapeHtml(u.cgi)} (earfcn_dl=${escapeHtml(String(u.earfcn_dl))}, src=${escapeHtml(u.src_table)})`).join('<br>')
        }${r.earfcn_unmapped_count > unmapped.length ? `<br><i>仅显示前 ${unmapped.length} 条, 共 ${r.earfcn_unmapped_count} 条未命中</i>` : ''}</div>`;

    const missingHtml = missingTables.length === 0
      ? ''
      : `<div class="section-title">未导入的源表</div>
         <div class="warn-list">${missingTables.map(escapeHtml).join('<br>')}</div>`;

    const success = !!r.success;
    const icon = success ? '✅' : '⚠️';
    const titleText = success ? '同步完成' : (r.message || '同步失败');

    const html = `
      <div class="modal-overlay" id="sync-result-modal">
        <div class="sync-modal">
          <span class="close-btn" onclick="document.getElementById('sync-result-modal').remove()">×</span>
          <h3><span class="icon">${icon}</span> ${escapeHtml(titleText)}</h3>

          <div class="summary">
            <div class="stat-card">
              <div class="label">生成 cgi 清单</div>
              <div class="value">${stats.cgi_count ?? '-'}</div>
            </div>
            <div class="stat-card success">
              <div class="label">匹配 cells.ecgi</div>
              <div class="value">${stats.matched_ecgis ?? 0}</div>
            </div>
            <div class="stat-card ${(stats.unmatched_ecgis || 0) > 0 ? 'warn' : 'success'}">
              <div class="label">未匹配 cells.ecgi</div>
              <div class="value">${stats.unmatched_ecgis ?? 0}</div>
            </div>
            <div class="stat-card success">
              <div class="label">更新 pci/tac</div>
              <div class="value">${stats.updated_pci_tac ?? 0}</div>
            </div>
            <div class="stat-card success">
              <div class="label">更新 earfcn/freq_band_ind</div>
              <div class="value">${stats.updated_earfcn_band ?? 0}</div>
            </div>
            <div class="stat-card success">
              <div class="label">标记已同步</div>
              <div class="value">${stats.marked_synced ?? stats.matched_ecgis ?? 0}</div>
            </div>
            <div class="stat-card ${(r.earfcn_unmapped_count || 0) > 0 ? 'warn' : 'success'}">
              <div class="label">earfcn_dl 未映射</div>
              <div class="value">${r.earfcn_unmapped_count ?? 0}</div>
            </div>
          </div>

          <div class="section-title">源表解析结果</div>
          ${sourceStatsHtml}

          ${missingHtml}

          <div class="section-title">earfcn_dl 未命中映射的样例 (前50条)</div>
          ${unmappedHtml}

          <div class="actions">
            <button class="primary" onclick="document.getElementById('sync-result-modal').remove()">关闭</button>
          </div>
        </div>
      </div>
    `;

    document.body.insertAdjacentHTML('beforeend', html);
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();

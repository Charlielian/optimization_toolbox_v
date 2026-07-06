// API封装
const API = {
  base: '',

  async uploadFile(file, options = {}) {
    // options.append: true=追加模式（4G/5G分两次上传），false=替换（默认）
    const form = new FormData();
    form.append('file', file);
    if (options.append) {
      form.append('append', 'true');
    }
    const res = await fetch(this.base + '/api/upload', {
      method: 'POST',
      body: form,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error('上传失败: ' + err);
    }
    return res.json();
  },

  /** 多文件工参导入；服务端解析后自动识别各文件 4G/5G */
  async uploadFiles(files, options = {}) {
    const list = Array.from(files || []);
    if (list.length === 0) throw new Error('未选择文件');
    if (list.length === 1) {
      return this.uploadFile(list[0], options);
    }
    const form = new FormData();
    list.forEach((f) => form.append('files', f));
    if (options.append) form.append('append', 'true');
    const res = await fetch(this.base + '/api/upload/batch', {
      method: 'POST',
      body: form,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error('批量上传失败: ' + err);
    }
    return res.json();
  },

  async planAll(params) {
    const res = await fetch(this.base + '/api/plan/all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async planPartial(params) {
    const res = await fetch(this.base + '/api/plan/partial', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async pciQuality(params = {}) {
    const res = await fetch(this.base + '/api/pci/quality', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async checkConflict(directionalFilter = true) {
    const res = await fetch(this.base + '/api/check/conflict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ directional_filter: directionalFilter }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async checkRedundancy() {
    const res = await fetch(this.base + '/api/check/redundancy', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async exportFile(exportType, vendor = 'huawei') {
    const res = await fetch(this.base + '/api/export/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ export_type: exportType, vendor }),
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    // 从header获取文件名
    const dispo = res.headers.get('Content-Disposition') || '';
    const m = dispo.match(/filename=([^;]+)/);
    const filename = m ? m[1].trim().replace(/"/g, '') : `export_${exportType}.xlsx`;
    return { blob, filename };
  },

  async getCells() {
    const res = await fetch(this.base + '/api/cells');
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async _parseApiError(res) {
    const text = await res.text();
    try {
      const j = JSON.parse(text);
      if (j.errors && Array.isArray(j.errors)) {
        return j.errors.join('; ');
      }
      if (j.detail) {
        if (typeof j.detail === 'string') return j.detail;
        if (typeof j.detail === 'object') {
          if (j.detail.errors && Array.isArray(j.detail.errors)) {
            return j.detail.errors.join('; ');
          }
          if (j.detail.detail) return String(j.detail.detail);
        }
      }
      if (j.errors && Array.isArray(j.errors)) return j.errors.join('; ');
    } catch (e) { /* ignore */ }
    return text || res.statusText;
  },

  async createCell(payload) {
    const res = await fetch(this.base + '/api/cells', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await this._parseApiError(res));
    return res.json();
  },

  async updateCell(ecgi, payload) {
    const res = await fetch(this.base + '/api/cells/' + encodeURIComponent(ecgi), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await this._parseApiError(res));
    return res.json();
  },

  async deleteCell(ecgi) {
    const res = await fetch(this.base + '/api/cells/' + encodeURIComponent(ecgi), {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(await this._parseApiError(res));
    return res.json();
  },

  async clearDb() {
    const res = await fetch(this.base + '/api/clear', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async downloadSample() {
    const res = await fetch(this.base + '/api/sample-data');
    if (!res.ok) throw new Error('示例文件不存在');
    return res.blob();
  },

  async downloadTemplate(rat = 'both') {
    const res = await fetch(`${this.base}/api/template?rat=${rat}`);
    if (!res.ok) throw new Error('模板下载失败');
    const blob = await res.blob();
    const dispo = res.headers.get('Content-Disposition') || '';
    const m = dispo.match(/filename=([^;]+)/);
    const filename = m ? m[1].trim().replace(/"/g, '') : `工参模板_${rat}.xlsx`;
    this.downloadBlob(blob, filename);
  },

  downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  // ──────────────────────────────────────────────
  // 单站 / 批量规划 / 干扰分析 (新)
  // ──────────────────────────────────────────────
  async planSingle(req, onProgress) {
    // 流式 (SSE): 服务端推送 progress 事件, 最后一个事件是 {type:'result', payload: {...}}
    const res = await fetch(this.base + '/api/plan/single/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!res.ok || !res.body) throw new Error(await res.text());
    return await _consumeSSE(res, onProgress);
  },

  async planBatch(file, options = {}, onProgress) {
    // 流式 (SSE): 服务端推送 progress 事件, 最后一个事件是 {type:'done', session_id}
    // 前端随后用 session_id 拉取 xlsx
    const form = new FormData();
    form.append('file', file);
    const opts = {
      nbr_plan_types: '4G_4G,4G_5G,5G_4G,5G_5G',
      engine: 'legacy',
      reuse_distance_km: 5.0,
      check_mod6: false,
      check_mod30: true,
      use_beam_overlap_score: false,
      directional_filter: true,
      ...options,
    };
    Object.entries(opts).forEach(([k, v]) => form.append(k, String(v)));

    const res = await fetch(this.base + '/api/plan/batch/stream', {
      method: 'POST',
      body: form,
    });
    if (!res.ok || !res.body) throw new Error(await res.text());
    const finalEvent = await _consumeSSE(res, onProgress);
    if (!finalEvent || finalEvent.type !== 'done' || !finalEvent.session_id) {
      throw new Error('规划未正常结束');
    }
    // 拉取 xlsx
    const dl = await fetch(this.base + '/api/plan/batch/result/' + encodeURIComponent(finalEvent.session_id));
    if (!dl.ok) throw new Error('下载批量规划结果失败: ' + (await dl.text()));
    const blob = await dl.blob();
    let filename = finalEvent.filename || 'batch_plan.xlsx';
    const dispo = dl.headers.get('Content-Disposition') || '';
    const m2 = dispo.match(/filename\*?=[^;]*UTF-8''([^;]+)/);
    if (m2) filename = decodeURIComponent(m2[1]);
    return { blob, filename, stats: finalEvent.stats || {} };
  },

  async exportSplit(plannedEcgis, nbrPlanTypes = null) {
    const res = await fetch(this.base + '/api/export/split', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        planned_ecgis: plannedEcgis,
        nbr_plan_types: nbrPlanTypes,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const dispo = res.headers.get('Content-Disposition') || '';
    let filename = 'plan_split.xlsx';
    const m = dispo.match(/filename\*?=[^;]*UTF-8''([^;]+)/);
    if (m) filename = decodeURIComponent(m[1]);
    else {
      const m2 = dispo.match(/filename="?([^";]+)"?/);
      if (m2) filename = m2[1];
    }
    return { blob, filename };
  },

  async analyzeInterference(params, opts = {}) {
    const { timeoutMs = 300000, signal: externalSignal } = opts;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error('analyze timeout')), timeoutMs);
    // 若外部传入 signal, 一并监听, 任一中断立即 abort
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort(externalSignal.reason);
      else externalSignal.addEventListener('abort', () => controller.abort(externalSignal.reason), { once: true });
    }
    try {
      const res = await fetch(this.base + '/api/interference/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    } finally {
      clearTimeout(timer);
    }
  },

  async exportInterference(params) {
    const res = await fetch(this.base + '/api/interference/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const dispo = res.headers.get('Content-Disposition') || '';
    let filename = 'interference.xlsx';
    const m = dispo.match(/filename\*?=[^;]*UTF-8''([^;]+)/);
    if (m) filename = decodeURIComponent(m[1]);
    else {
      const m2 = dispo.match(/filename="?([^";]+)"?/);
      if (m2) filename = m2[1];
    }
    return { blob, filename };
  },

  // ──────────────────────────────────────────────
  // 配置导入管理
  // ──────────────────────────────────────────────
  async configUpload(file, sheets = '') {
    const form = new FormData();
    form.append('file', file);
    if (sheets) form.append('sheets', sheets);
    const res = await fetch(this.base + '/api/config/upload', {
      method: 'POST',
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // 流式导入 (SSE): 服务端推送 progress 事件，最后一个事件是 {type:'done', payload: {...}}
  // onProgress(payload) 接收后端原始事件 (含 stage/pct/sheet/row/row_total 等)
  async configUploadStream(file, sheets = '', onProgress) {
    const form = new FormData();
    form.append('file', file);
    if (sheets) form.append('sheets', sheets);
    const res = await fetch(this.base + '/api/config/upload/stream', {
      method: 'POST',
      body: form,
    });
    if (!res.ok || !res.body) throw new Error(await res.text());
    // 直接传 onProgress 让 _consumeSSE 根据 .length 决定调用方式
    // (新接口: 单参数 payload, .length === 1)
    return await _consumeSSE(res, onProgress);
  },

  async configGetTables() {
    const res = await fetch(this.base + '/api/config/tables');
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configGetData(tableName, page = 1, pageSize = 50, keyword = '') {
    let url = `${this.base}/api/config/data/${encodeURIComponent(tableName)}?page=${page}&page_size=${pageSize}`;
    if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configGetHistory(limit = 100) {
    const res = await fetch(this.base + '/api/config/history?limit=' + limit);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configDeleteTable(tableName) {
    const res = await fetch(this.base + '/api/config/table/' + encodeURIComponent(tableName), {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configGetSheetConfigs() {
    const res = await fetch(this.base + '/api/config/sheet-configs');
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configGetSheetConfig(sheetName) {
    const res = await fetch(this.base + '/api/config/sheet-config/' + encodeURIComponent(sheetName));
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configSaveSheetConfig(sheetName, columns, enabled, description) {
    const res = await fetch(this.base + '/api/config/sheet-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sheet_name: sheetName,
        columns,
        enabled: enabled !== undefined ? enabled : true,
        description: description || '',
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configDeleteSheetConfig(sheetName) {
    const res = await fetch(this.base + '/api/config/sheet-config/' + encodeURIComponent(sheetName), {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configReload() {
    const res = await fetch(this.base + '/api/config/reload', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configSyncCells() {
    const res = await fetch(this.base + '/api/config/sync-cells', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async configParseExcel(file) {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(this.base + '/api/config/parse-excel', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
};

// ──────────────────────────────────────────────
// SSE 解析辅助: 把 fetch + ReadableStream 转成 JSON 事件序列
// 约定服务端 event:
//   {type:"progress", pct:0-100, stage:"..."}   → 中间进度
//   {type:"result"|"done", ...payload}         → 终结事件, 函数返回该对象
//   {type:"error", message:"..."}              → 抛错
// ──────────────────────────────────────────────
async function _consumeSSE(response, onProgress) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  let finalEvent = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE 事件以空行分隔
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      // 解析 event/data
      let eventName = 'message';
      let dataLines = [];
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      const dataStr = dataLines.join('\n');
      if (!dataStr) continue;
      let payload = null;
      try { payload = JSON.parse(dataStr); } catch (e) { payload = { raw: dataStr }; }

      if (eventName === 'progress') {
        if (typeof onProgress === 'function') {
          // 兼容两种调用:
          //   旧: onProgress(pct, stage)
          //   新: onProgress(payload) — 传整个 payload (含 sheet/row/row_total 等)
          if (onProgress.length >= 2) {
            onProgress(payload.pct || 0, payload.stage || '');
          } else {
            onProgress(payload);
          }
        }
      } else if (eventName === 'result' || eventName === 'done') {
        finalEvent = payload;
        // 关闭流
        try { reader.cancel(); } catch (e) {}
        return finalEvent;
      } else if (eventName === 'error') {
        try { reader.cancel(); } catch (e) {}
        throw new Error(payload.message || '规划失败');
      }
    }
  }
  return finalEvent;
}
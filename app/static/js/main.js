/* 视频智能剪辑系统 - 前端逻辑 */

const API = '/api/videos';
let currentVideoId = null;
let transcriptSegments = [];

// ─── 工具 ───
function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 3000);
}

function fmtTime(s) {
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1);
    return `${String(m).padStart(2, '0')}:${sec.padStart(4, '0')}`;
}

function statusBadge(status) {
    const map = { uploaded: '已上传', transcribing: '转写中', transcribed: '已转写', processing: '处理中', done: '已完成', error: '出错' };
    return `<span class="badge ${status}">${map[status] || status}</span>`;
}

async function api(url, opts = {}) {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || '请求失败');
    }
    return resp.json();
}

// ─── 视频列表 ───
async function loadVideos() {
    try {
        const list = await api(API);
        const container = document.getElementById('videoList');
        if (list.length === 0) {
            container.innerHTML = '<div class="empty">暂无视频，请上传</div>';
            return;
        }
        container.innerHTML = list.map(v => `
            <div class="video-card" data-id="${v.id}">
                <div class="vc-header">
                    <span class="vc-name">${v.filename}</span>
                    ${statusBadge(v.status)}
                </div>
                <div class="vc-meta">时长 ${fmtTime(v.duration)} · ${v.created_at.slice(0, 19).replace('T', ' ')}</div>
                <div class="btn-row">
                    <button class="btn btn-primary btn-sm" onclick="selectVideo('${v.id}','${v.filename.replace(/'/g,"\\'")}')">选择</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteVideo('${v.id}')">删除</button>
                </div>
            </div>
        `).join('');
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 上传 ───
function setupUpload() {
    const zone = document.getElementById('uploadZone');
    const input = document.getElementById('fileInput');

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
    });
    input.addEventListener('change', () => {
        if (input.files.length) uploadFile(input.files[0]);
    });
}

async function uploadFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    try {
        toast('上传中...', 'info');
        const data = await api(`${API}/upload`, { method: 'POST', body: fd });
        toast('上传成功', 'success');
        await loadVideos();
        selectVideo(data.id, data.filename);
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 选择视频 ───
async function selectVideo(id, name) {
    currentVideoId = id;
    document.getElementById('currentVideo').textContent = name;
    document.getElementById('workspace').classList.remove('collapsed');
    // 滚动到工作区
    document.getElementById('workspace').scrollIntoView({ behavior: 'smooth' });
    // 加载字幕和结果
    await loadTranscript();
    await loadResults();
    await loadTemplates();
}

// ─── 转写 ───
async function startTranscribe() {
    if (!currentVideoId) return;
    try {
        toast('转写已开始，请等待...', 'info');
        await api(`${API}/${currentVideoId}/transcribe`, { method: 'POST' });
        // 轮询状态
        pollStatus(currentVideoId, () => { loadVideos(); loadTranscript(); });
    } catch (e) { toast(e.message, 'error'); }
}

async function uploadSubtitle() {
    const input = document.getElementById('subtitleFile');
    if (!input.files.length) { toast('请选择字幕文件', 'error'); return; }
    const fd = new FormData();
    fd.append('file', input.files[0]);
    try {
        toast('导入中...', 'info');
        await api(`${API}/${currentVideoId}/upload-subtitle`, { method: 'POST', body: fd });
        toast('字幕导入成功', 'success');
        await loadVideos();
        await loadTranscript();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── OCR 字幕提取 + 融合 ───
async function ocrExtract() {
    if (!currentVideoId) return;
    try {
        toast('OCR 提取中，请等待...', 'info');
        const data = await api(`${API}/${currentVideoId}/ocr-extract`, { method: 'POST' });
        toast(`OCR 完成：识别到 ${data.count} 段字幕`, 'success');
        await loadTranscript();
    } catch (e) { toast(e.message, 'error'); }
}

async function fuseSubtitle() {
    if (!currentVideoId) return;
    try {
        toast('正在融合 OCR + 语音...', 'info');
        const data = await api(`${API}/${currentVideoId}/fuse-subtitle`, { method: 'POST' });
        toast(`融合完成：OCR ${data.ocr_count} 段 + ASR ${data.asr_count} 段 -> ${data.count} 段`, 'success');
        await loadTranscript();
    } catch (e) { toast(e.message, 'error'); }
}

// ═══════════ 学习系统 ═══════════
const LEARN_API = '/api/learn';
let analysisData = null;       // 当前分析结果
let roleDeleteSet = new Set(); // 标记删除的角色

// 角色中文标签
const ROLE_LABELS = {
    hook: '开头钩子', pain_point: '痛点', product: '产品介绍', evidence: '案例证据',
    summary: '总结', cta: '行动号召', filler: '废话填充', transition: '过渡衔接',
    repeat: '重复内容', content: '正文核心',
};

// 角色颜色
const ROLE_COLORS = {
    hook: '#e17055', pain_point: '#fdcb6e', product: '#00b894', evidence: '#0984e3',
    summary: '#6c5ce7', cta: '#e84393', filler: '#b2bec3', transition: '#a29bfe',
    repeat: '#dfe6e9', content: '#55efc4',
};

async function analyzeScript() {
    if (!currentVideoId) { toast('请先选择视频', 'error'); return; }
    const statusEl = document.getElementById('analyzeStatus');
    statusEl.textContent = '正在分析文案结构...';
    try {
        const data = await api(`${LEARN_API}/${currentVideoId}/analyze`, { method: 'POST' });
        analysisData = data;
        // 默认删除 filler + repeat
        roleDeleteSet = new Set(data.template_suggestion.delete_roles || ['filler', 'repeat']);
        renderAnalysisResult(data);
        statusEl.textContent = `分析完成：${data.segment_count} 段`;
        toast(`分析完成：${data.segment_count} 段，建议删除 ${roleDeleteSet.size} 种角色`, 'success');
    } catch (e) {
        statusEl.textContent = '';
        toast(e.message, 'error');
    }
}

function renderAnalysisResult(data) {
    document.getElementById('analysisResult').style.display = 'block';
    const sug = data.template_suggestion || {};
    const stats = sug.role_stats || {};

    // 角色分布卡片
    const statsEl = document.getElementById('roleStats');
    statsEl.innerHTML = Object.entries(stats).map(([role, st]) => {
        const isDelete = roleDeleteSet.has(role);
        const color = ROLE_COLORS[role] || '#636e72';
        return `<div class="role-card ${isDelete ? 'role-delete' : 'role-keep'}"
                    onclick="toggleRole('${role}')"
                    style="cursor:pointer;border:2px solid ${isDelete ? '#e74c3c' : color};
                           background:${isDelete ? '#fff5f5' : color + '11'};
                           border-radius:8px;padding:8px 12px;min-width:120px;">
            <div style="font-weight:bold;color:${color};">${ROLE_LABELS[role] || role}</div>
            <div style="font-size:12px;color:#636e72;">${st.count}段 · ${(st.ratio*100).toFixed(0)}%</div>
            <div style="font-size:11px;color:${isDelete ? '#e74c3c' : '#00b894'};font-weight:bold;">
                ${isDelete ? '✗ 删除' : '✓ 保留'}
            </div>
        </div>`;
    }).join('');

    // 段落分析列表
    const segsEl = document.getElementById('analyzedSegments');
    segsEl.innerHTML = data.analyzed_segments.map(s => {
        const role = s.role || 'content';
        const color = ROLE_COLORS[role] || '#636e72';
        const isDelete = roleDeleteSet.has(role);
        return `<div style="padding:4px 6px;border-bottom:1px solid #f0f0f0;${isDelete ? 'opacity:0.4;text-decoration:line-through;' : ''}">
            <span style="font-size:11px;color:#999;">${fmtTime(s.start)}→${fmtTime(s.end)}</span>
            <span style="display:inline-block;padding:1px 6px;border-radius:4px;background:${color}22;color:${color};font-size:11px;margin:0 4px;">${ROLE_LABELS[role] || role}</span>
            <span style="font-size:13px;">${s.text}</span>
        </div>`;
    }).join('');
}

function toggleRole(role) {
    if (roleDeleteSet.has(role)) roleDeleteSet.delete(role);
    else roleDeleteSet.add(role);
    // 重新渲染
    if (analysisData) renderAnalysisResult(analysisData);
}

async function saveTemplate() {
    if (!analysisData) { toast('请先学习分析', 'error'); return; }
    const name = document.getElementById('tplName').value.trim();
    if (!name) { toast('请输入模板名称', 'error'); return; }

    const deleteRoles = [...roleDeleteSet];
    const keepRoles = Object.keys(analysisData.template_suggestion.role_stats || {})
        .filter(r => !roleDeleteSet.has(r));

    try {
        const data = await api(`${LEARN_API}/templates`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                description: `来源: ${analysisData.video_name || ''}`,
                source_video_id: currentVideoId,
                delete_roles: deleteRoles,
                keep_roles: keepRoles,
                role_stats: analysisData.template_suggestion.role_stats || {},
                analyzed_segments: analysisData.analyzed_segments || [],
            }),
        });
        toast(`模板「${name}」保存成功`, 'success');
        document.getElementById('tplName').value = '';
        await loadTemplates();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadTemplates() {
    try {
        const list = await api(`${LEARN_API}/templates`);
        // 渲染模板列表
        const box = document.getElementById('templateList');
        if (!list.length) {
            box.innerHTML = '<div class="empty">暂无模板，请先学习分析并保存</div>';
        } else {
            box.innerHTML = list.map(t => `
                <div class="result-item">
                    <div class="result-info">
                        <strong>${t.name}</strong>
                        · 删除: ${(t.delete_roles||[]).map(r=>ROLE_LABELS[r]||r).join('、') || '无'}
                        · ${t.segment_count}段分析
                        · ${t.created_at.slice(0,19).replace('T',' ')}
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="deleteTemplate('${t.id}')">删除</button>
                </div>
            `).join('');
        }
        // 填充剪辑下拉框
        const sel = document.getElementById('templateSelect');
        sel.innerHTML = '<option value="">-- 选择模板 --</option>' +
            list.map(t => `<option value="${t.id}">${t.name} (删${(t.delete_roles||[]).length}类)</option>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteTemplate(id) {
    if (!confirm('确认删除该模板？')) return;
    try {
        await api(`${LEARN_API}/templates/${id}`, { method: 'DELETE' });
        toast('模板已删除', 'success');
        await loadTemplates();
    } catch (e) { toast(e.message, 'error'); }
}

async function clipByTemplate() {
    if (!currentVideoId) { toast('请先选择视频', 'error'); return; }
    const tplId = document.getElementById('templateSelect').value;
    if (!tplId) { toast('请选择学习模板', 'error'); return; }
    const margin = parseFloat(document.getElementById('learnMargin').value) || 0.2;
    const alsoSilence = document.getElementById('learnAlsoSilence').checked;
    try {
        toast('正在按学习模板剪辑...', 'info');
        const data = await api(`${LEARN_API}/${currentVideoId}/clip-by-template`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: tplId, margin, also_remove_silence: alsoSilence }),
        });
        toast(`完成：删除${data.removed_segments}段废话，保留${data.kept_segments}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 字幕 ───
async function loadTranscript() {
    if (!currentVideoId) return;
    try {
        const data = await api(`${API}/${currentVideoId}/transcript`);
        transcriptSegments = data.segments || [];
        renderTranscript(transcriptSegments, []);
    } catch (e) {
        transcriptSegments = [];
        document.getElementById('transcriptBox').innerHTML = '<div class="empty">暂无字幕</div>';
    }
}

function renderTranscript(segs, hitIndices = []) {
    const box = document.getElementById('transcriptBox');
    if (!segs.length) {
        box.innerHTML = '<div class="empty">暂无字幕</div>';
        return;
    }
    const hitSet = new Set(hitIndices);
    box.innerHTML = segs.map((s, i) => `
        <div class="seg-item ${hitSet.has(i) ? 'seg-hit' : ''}">
            <span class="seg-time">${fmtTime(s.start)} → ${fmtTime(s.end)}</span>
            <span class="seg-text">${s.text}</span>
        </div>
    `).join('');
}

// ─── 工具：获取随机删除参数 ───
function getRandomParams() {
    return {
        random_delete: document.getElementById('randomDelete').checked,
        min_ratio: parseFloat(document.getElementById('minRatio').value) || 0.5,
        max_ratio: parseFloat(document.getElementById('maxRatio').value) || 1.0,
        smart_gap: document.getElementById('smartGap').checked,
    };
}

// ─── 静音删除 ───
async function clipSilence() {
    if (!currentVideoId) return;
    const noiseDb = document.getElementById('noiseDb').value;
    const minDur = document.getElementById('minDuration').value;
    const body = { ...getRandomParams() };
    if (noiseDb) body.noise_db = parseFloat(noiseDb);
    if (minDur) body.min_duration = parseFloat(minDur);
    try {
        toast('正在删除静音片段...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-silence`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        toast(`完成：保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 删除无文字部分 ───
async function clipNoText() {
    if (!currentVideoId) return;
    const margin = parseFloat(document.getElementById('margin').value) || 0.2;
    const body = { margin, ...getRandomParams() };
    try {
        toast('正在删除无文字片段...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-no-text`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        toast(`完成：保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 关键词删除 ───
function previewKeywords() {
    const raw = document.getElementById('keywords').value;
    const kws = raw.split(/[,，\n]/).map(k => k.trim()).filter(Boolean);
    if (!kws.length || !transcriptSegments.length) {
        renderTranscript(transcriptSegments, []);
        toast('请输入关键词', 'info');
        return;
    }
    const hits = [];
    transcriptSegments.forEach((s, i) => {
        if (kws.some(k => s.text.includes(k))) hits.push(i);
    });
    renderTranscript(transcriptSegments, hits);
    toast(`命中 ${hits.length} 段`, hits.length ? 'error' : 'success');
}

async function clipKeywords() {
    if (!currentVideoId) return;
    const raw = document.getElementById('keywords').value;
    const kws = raw.split(/[,，\n]/).map(k => k.trim()).filter(Boolean);
    if (!kws.length) { toast('请输入关键词', 'error'); return; }
    const margin = parseFloat(document.getElementById('margin').value) || 0.3;
    try {
        toast('正在按关键词剪辑...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-keywords`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords: kws, margin }),
        });
        toast(`完成：保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 组合剪辑 ───
async function clipCombo() {
    if (!currentVideoId) return;
    const raw = document.getElementById('keywords').value;
    const kws = raw.split(/[,，\n]/).map(k => k.trim()).filter(Boolean);
    const noiseDb = document.getElementById('noiseDb').value;
    const minDur = document.getElementById('minDuration').value;
    const margin = parseFloat(document.getElementById('margin').value) || 0.3;
    const removeNoText = document.getElementById('removeNoText').checked;
    const body = { keywords: kws, margin, remove_no_text: removeNoText, ...getRandomParams() };
    if (noiseDb) body.noise_db = parseFloat(noiseDb);
    if (minDur) body.min_duration = parseFloat(minDur);
    try {
        toast('正在组合剪辑...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-combo`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        toast(`完成：保留${data.keep_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 结果 ───
async function loadResults() {
    if (!currentVideoId) return;
    try {
        const list = await api(`${API}/${currentVideoId}/results`);
        const box = document.getElementById('resultList');
        if (!list.length) { box.innerHTML = '<div class="empty">暂无剪辑结果</div>'; return; }
        const typeMap = { silence: '删除静音', keyword: '删除关键词', no_text: '删除无文字', combo: '组合剪辑', learn: '学习剪辑' };
        box.innerHTML = list.map(r => `
            <div class="result-item">
                <div class="result-info">
                    <span class="ri-type">${typeMap[r.clip_type] || r.clip_type}</span>
                    · 保留${r.keep_count}段 · ${r.created_at.slice(0, 19).replace('T',' ')}
                </div>
                <a class="btn btn-success btn-sm" href="${API}/${currentVideoId}/download/${r.id}" target="_blank">下载</a>
            </div>
        `).join('');
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 删除 ───
async function deleteVideo(id) {
    if (!confirm('确认删除该视频及所有数据？')) return;
    try {
        await api(`${API}/${id}`, { method: 'DELETE' });
        toast('已删除', 'success');
        if (currentVideoId === id) {
            currentVideoId = null;
            document.getElementById('workspace').classList.add('collapsed');
        }
        await loadVideos();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 轮询 ───
function pollStatus(videoId, onDone) {
    const timer = setInterval(async () => {
        try {
            const data = await api(`${API}/${videoId}`);
            if (data.status === 'transcribed' || data.status === 'done' || data.status === 'error') {
                clearInterval(timer);
                if (data.status === 'error') toast('转写失败', 'error');
                else toast('转写完成', 'success');
                onDone();
            }
        } catch (e) { clearInterval(timer); }
    }, 3000);
}

// ─── 初始化 ───
document.addEventListener('DOMContentLoaded', () => {
    setupUpload();
    loadVideos();
    loadTemplates();
});

/* 智能剪辑页面逻辑 */

// 页面加载/选择视频时拉模板下拉 + 结果
function onPageLoad() { loadTemplateSelect(); }
function onPageSelect() { loadTemplateSelect(); loadResults(); }

// ─── 按学习模板剪辑 ───
async function loadTemplateSelect() {
    try {
        const list = await api(`${LEARN_API}/templates`);
        const sel = document.getElementById('templateSelect');
        sel.innerHTML = '<option value="">-- 选择模板 --</option>' +
            list.map(t => `<option value="${t.id}">${t.name} (删${(t.delete_roles||[]).length}类)</option>`).join('');
    } catch (e) { /* 静默 */ }
}

async function clipByTemplate() {
    if (!currentVideoId) { toast('请先选择视频', 'error'); return; }
    const tplId = document.getElementById('templateSelect').value;
    if (!tplId) { toast('请选择学习模板', 'error'); return; }
    const margin = parseFloat(document.getElementById('learnMargin').value) || 0.2;
    const alsoSilence = document.getElementById('learnAlsoSilence').checked;
    try {
        toast('正在按学习模板剪辑（LLM分析中，请等待）...', 'info');
        const data = await api(`${LEARN_API}/${currentVideoId}/clip-by-template`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: tplId, margin, also_remove_silence: alsoSilence }),
        });
        toast(`完成：删除${data.removed_segments}段，保留${data.kept_segments}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 随机参数 ───
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
    const body = { ...getRandomParams() };
    const nd = document.getElementById('noiseDb').value;
    const md = document.getElementById('minDuration').value;
    if (nd) body.noise_db = parseFloat(nd);
    if (md) body.min_duration = parseFloat(md);
    try {
        toast('正在删除静音...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-silence`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        toast(`保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 删除无文字 ───
async function clipNoText() {
    if (!currentVideoId) return;
    const margin = parseFloat(document.getElementById('margin').value) || 0.2;
    const body = { margin, ...getRandomParams() };
    try {
        toast('正在删除无文字片段...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-no-text`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        toast(`保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 关键词 ───
function previewKeywords() {
    const raw = document.getElementById('keywords').value;
    const kws = raw.split(/[,，\n]/).map(k => k.trim()).filter(Boolean);
    if (!kws.length || !transcriptSegments.length) { renderTranscript(transcriptSegments, []); return; }
    const hits = [];
    transcriptSegments.forEach((s, i) => { if (kws.some(k => s.text.includes(k))) hits.push(i); });
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
        toast(`保留${data.keep_count}段，删除${data.removed_count}段`, 'success');
        await loadResults();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 组合 ───
async function clipCombo() {
    if (!currentVideoId) return;
    const raw = document.getElementById('keywords').value;
    const kws = raw.split(/[,，\n]/).map(k => k.trim()).filter(Boolean);
    const margin = parseFloat(document.getElementById('margin').value) || 0.3;
    const body = { keywords: kws, margin, remove_no_text: document.getElementById('removeNoText').checked, ...getRandomParams() };
    const nd = document.getElementById('noiseDb').value;
    const md = document.getElementById('minDuration').value;
    if (nd) body.noise_db = parseFloat(nd);
    if (md) body.min_duration = parseFloat(md);
    try {
        toast('正在组合剪辑...', 'info');
        const data = await api(`${API}/${currentVideoId}/clip-combo`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        toast(`保留${data.keep_count}段`, 'success');
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
                    · 保留${r.keep_count}段 · ${r.created_at.slice(0,19).replace('T',' ')}
                </div>
                <a class="btn btn-success btn-sm" href="${API}/${currentVideoId}/download/${r.id}" target="_blank">下载</a>
            </div>
        `).join('');
    } catch (e) { toast(e.message, 'error'); }
}

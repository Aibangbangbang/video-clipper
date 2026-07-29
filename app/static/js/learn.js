/* 学习中心页面逻辑 */
let analysisData = null;
let roleDeleteSet = new Set();

const ROLE_COLORS = {
    hook: '#e17055', pain_point: '#fdcb6e', product: '#00b894', evidence: '#0984e3',
    summary: '#6c5ce7', cta: '#e84393', filler: '#b2bec3', transition: '#a29bfe',
    repeat: '#dfe6e9', content: '#55efc4',
};

// 页面加载时拉模板列表
function onPageLoad() { loadTemplates(); }
function onPageSelect() { loadTemplates(); }

// ─── 学习分析 ───
async function analyzeScript() {
    if (!currentVideoId) { toast('请先选择视频', 'error'); return; }
    const statusEl = document.getElementById('analyzeStatus');
    statusEl.textContent = '正在分析文案结构...';
    try {
        const data = await api(`${LEARN_API}/${currentVideoId}/analyze`, { method: 'POST' });
        analysisData = data;
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

    const statsEl = document.getElementById('roleStats');
    statsEl.innerHTML = Object.entries(stats).map(([role, st]) => {
        const isDelete = roleDeleteSet.has(role);
        const color = ROLE_COLORS[role] || '#636e72';
        return `<div class="role-card" onclick="toggleRole('${role}')"
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
    if (analysisData) renderAnalysisResult(analysisData);
}

// ─── 保存模板 ───
async function saveTemplate() {
    if (!analysisData) { toast('请先学习分析', 'error'); return; }
    const name = document.getElementById('tplName').value.trim();
    if (!name) { toast('请输入模板名称', 'error'); return; }
    const deleteRoles = [...roleDeleteSet];
    const keepRoles = Object.keys(analysisData.template_suggestion.role_stats || {})
        .filter(r => !roleDeleteSet.has(r));
    try {
        await api(`${LEARN_API}/templates`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, description: `来源: ${analysisData.video_name || ''}`,
                source_video_id: currentVideoId,
                delete_roles: deleteRoles, keep_roles: keepRoles,
                role_stats: analysisData.template_suggestion.role_stats || {},
                analyzed_segments: analysisData.analyzed_segments || [],
            }),
        });
        toast(`模板「${name}」保存成功`, 'success');
        document.getElementById('tplName').value = '';
        await loadTemplates();
    } catch (e) { toast(e.message, 'error'); }
}

// ─── 模板列表 ───
async function loadTemplates() {
    try {
        const list = await api(`${LEARN_API}/templates`);
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

/* 只读展示由 export_records.py 导出的文件快照。不会调用 crew.py 或自行放行。 */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {released:'历史放行记录', rejected:'退回修改', invalid:'本轮失效', interrupted:'本轮中断（非审查结论，不计配额）', superseded_reject:'退回（已被下一轮取代）', user_accepted:'用户裁决通过', superseded:'已被下一轮取代', pending:'等待审查 / 裁决', needs_user:'需要用户裁决', todo:'尚未送审', unknown:'记录待核对', published:'已公示 · 设计阶段'};
  const WORKFLOW = '设计：Codex 写 → Claude 修订 → 看板公示（不走审查） · 代码：Claude 写 → dsh 初审 + Codex 终审';
  const PROJECT_KEY = 'crew-studio-project';
  let projects = [], currentProject = null;
  const recordsUrl = () => location.protocol === 'file:' ? 'data/local-records.json' : 'records?project=' + encodeURIComponent(currentProject || '');
  const accepted = (t, r) => t.user_decision && t.user_decision.decision === 'accept' && t.user_decision.round === r.number;
  const isDone = s => s === 'released' || s === 'user_accepted' || s === 'published';
  const extraRounds = t => (t.approvals || []).filter(a => a && a.kind === 'extra_round' && a.applied === true);
  const externals = r => Object.values(r?.reviewers || {}).flatMap(rv => rv.attempts.filter(a => a.external));
  const outcomes = {pass:'通过', reject:'退回', incomplete:'未完成'};
  const issueLabels = {open:'未关闭', closing:'关闭中 · 待本轮放行', closed:'已关闭'};
  const original = {};
  const savedSelectors = ['.room-meta','.evidence-strip','#tasks-view .section-label','#issues-view .section-label','#ledger-view .section-label','.footer>span','.local-note','.project-switch','.breadcrumb strong'];
  savedSelectors.forEach(s => original[s] = $(s).innerHTML);
  let snapshot = null, selectedTask = null, selectedRound = null, tab = 'gate', issueFilter = 'all', attempts = {};
  const api = window.crewRecords = {active:false, render:renderRecords, help:help};
  const latest = t => t.rounds.at(-1);
  const allIssues = () => snapshot.tasks.flatMap(t => t.issues.map(i => ({...i, task:t.id})));
  const unresolved = t => t.issues.filter(i => i.status !== 'closed');
  const getTask = id => snapshot.tasks.find(t => t.id === id);
  const getRound = () => selectedTask?.rounds.find(r => r.number === selectedRound);
  function state(t) {
    if (t.review === 'draft') return 'published';
    const r = latest(t);
    if (!r) return 'todo';
    if (r.invalid) return r.interrupted ? 'interrupted' : 'invalid';
    const g = r.gate;
    if (!g) return r.release ? 'unknown' : 'pending';
    if (g.task !== t.id || g.round !== r.number || g.fingerprint !== r.fingerprint) return 'unknown';
    if (!Object.hasOwn(labels,g.status) || (g.status === 'released' && (!r.release || r.release.fingerprint !== r.fingerprint))) return 'unknown';
    if (g.status === 'needs_user' && accepted(t, r)) return 'user_accepted';
    return g.status;
  }
  const status = s => `<span class="status-label ${isDone(s)?'pass':['rejected','invalid','needs_user','unknown','superseded_reject'].includes(s)?'reject':['interrupted','superseded'].includes(s)?'ext':s==='todo'?'todo':'review'}"><i class="dot"></i>${esc(labels[s]||'未知状态')}</span>`;
  function filtered(t) {
    const query = $('#search').value.trim().toLowerCase();
    return (!pendingOnly || !isDone(state(t))) && `${t.id} ${t.name} ${t.summary||""}`.toLowerCase().includes(query);
  }
  function reviewerLine(name, item) {
    const adopted = item.adopted;
    const a = item.attempts.find(a => a.number === adopted?.attempt);
    const outcome = a?.verdict?.verdict;
    const consistent = adopted && a?.meta && a.meta.outcome === adopted.verdict && outcome === adopted.verdict;
    const lastExt = !consistent && item.attempts.at(-1)?.external;
    const txt = consistent ? `${outcomes[outcome]||'未知'} · 采用 A${String(a.number).padStart(2,'0')}` : lastExt ? `外部故障：${lastExt} · 不计结论` : item.attempts.length ? '未采用 / 记录待核对' : '未开始';
    return `<div class="agent-line"><strong>${esc(name)}</strong><span class="agent-state ${lastExt?'ext':''}">${esc(txt)}</span></div>`;
  }
  function draftCard(t) {
    return `<article class="task-card done" role="button" tabindex="0" data-record-task="${esc(t.id)}" aria-label="查看任务 ${esc(t.id)}"><div class="card-top"><span class="card-id">${esc(t.id)}</span><span class="round-tag">设计</span></div><div class="record-card-status">${status('published')}</div><h3>${esc(t.name)}</h3>${t.summary?`<p class="record-one-line">${esc(t.summary)}</p>`:''}<p>${esc(t.author)} 起草 → ${esc(t.reviser)} 修订 → 公示 · 不走审查</p><div class="card-tags">${t.files.map(f=>`<span class="chip">${esc(f.label)}</span>`).join('')}${t.revision_log?'<span class="chip amber">有修订记录</span>':'<span class="chip coral">缺修订记录</span>'}</div><div class="card-bottom"><span class="card-type">${icon('lock')}原稿 / 修订版 / 修订记录</span><span class="record-card-link">查看公示 ${icon('arrow')}</span></div></article>`;
  }
  function card(t) {
    if (t.review === 'draft') return draftCard(t);
    const r = latest(t), s = state(t), count = unresolved(t).length;
    return `<article class="task-card ${isDone(s)?'done':s==='pending'?'active':['needs_user','invalid','rejected','unknown'].includes(s)?'blocked record-blocked':''}" role="button" tabindex="0" data-record-task="${esc(t.id)}" aria-label="查看任务 ${esc(t.id)}"><div class="card-top"><span class="card-id">${esc(t.id)}</span><span class="round-tag">${r?'R'+String(r.number).padStart(2,'0'):'未开轮'}</span></div><div class="record-card-status">${status(s)}</div><h3>${esc(t.name)}</h3>${t.summary?`<p class="record-one-line">${esc(t.summary)}</p>`:''}<p>${r?`第 ${r.number} 轮（计入配额 ${r.counted ?? '?'}/${t.max_rounds ?? '?'}）· ${(r?.rules.review||t.review)==='double'?'独立双审':'单审'}`:'配置已登记，尚无轮次记录'}</p>${r?`<div class="agent-pair">${Object.entries(r.reviewers).map(([by,value])=>reviewerLine(by,value)).join('')}</div>`:''}<div class="card-tags"><span class="chip ${count?'coral':''}">${count} 条待处理台账</span><span class="chip">${t.rounds.length} 轮记录</span>${t.rulings?'<span class="chip amber">有用户裁决</span>':''}${extraRounds(t).length?`<span class="chip">用户批准追加 ${extraRounds(t).length} 轮</span>`:''}${externals(r).length?`<span class="chip amber">外部故障 ${externals(r).length} 次</span>`:''}</div><div class="card-bottom"><span class="card-type">${icon('lock')}当前文件一致性未复核</span><span class="record-card-link">查看记录 ${icon('arrow')}</span></div></article>`;
  }
  function renderRecords() {
    if (!snapshot) return;
    const tasks = snapshot.tasks, issues = allIssues(), released = tasks.filter(t=>state(t)==='released').length;
    const pending = issues.filter(i=>i.status!=='closed').length;
    $('#search').placeholder='搜索登记任务…';
    $('#board').innerHTML = [['todo','尚未送审','配置登记'],['review','审查与待处理','以文件记录为准'],['done','放行与公示','当前版本未复核']].map(([key,title,note])=>{
      const list=tasks.filter(filtered).filter(t=>key==='todo'?state(t)==='todo':key==='done'?isDone(state(t)):!isDone(state(t))&&state(t)!=='todo');
      return `<section class="board-column column-${key}" aria-label="${title}"><div class="column-heading"><i class="status-dot"></i>${title}<span class="column-count">${list.length}</span><span class="column-meta">${note}</span></div><div class="card-stack">${list.length?list.map(card).join(''):`<div class="empty-column">${key==='done'?'没有匹配的历史放行记录':'没有匹配的登记任务'}</div>`}</div></section>`;
    }).join('');
    const filteredTasks=tasks.filter(filtered);
    $('#task-body').innerHTML=filteredTasks.length?filteredTasks.map(t=>`<tr class="task-row" role="button" tabindex="0" data-record-task="${esc(t.id)}" aria-label="查看任务 ${esc(t.id)}"><td class="mono">${esc(t.id)}</td><td><div class="task-name">${esc(t.summary||t.name)}</div><div class="task-note">${t.rounds.length} 轮记录 · ${unresolved(t).length} 条待处理台账</div></td><td class="mono">${latest(t)?'R'+String(latest(t).number).padStart(2,'0'):'—'}</td><td>${status(state(t))}</td><td>${esc(t.review==='draft'?'Codex 写 · Claude 修订':t.review==='double'?'dsh + Codex':'dsh')}</td></tr>`).join(''):'<tr><td colspan="5" class="empty-column">没有匹配的登记任务</td></tr>';
    $('.room-meta').innerHTML=`<div class="meta-items"><span class="meta-item">${icon('layers')}<strong>${tasks.length}</strong>登记任务</span><span class="meta-item">${icon('check-circle')}<strong>${released}</strong>历史放行</span><span class="meta-item">${icon('flag')}<strong style="color:var(--coral)">${pending}</strong>待处理台账</span></div><div class="presence">${esc(snapshot.project_key||snapshot.project_name)} · ${esc(WORKFLOW)}</div>`;
    $('.side-progress-head>span').textContent='当前源码核对';$('#progress-number').textContent='未复核';$('#progress-fill').style.width='0';$('#side-summary').textContent=`${released} 项有历史放行记录，当前有效性未确认`;
    $('[data-nav="tasks"] .nav-count').textContent=tasks.length;$('[data-nav="issues"] .nav-count').textContent=pending;
    $('.local-note').innerHTML='<i class="dot"></i>文件快照 · 不执行调度命令';
    $('#tasks-view .section-label').innerHTML=`<span>配置里登记的任务</span><span class="mono">${tasks.length} REGISTERED</span>`;
    const target=tasks.find(t=>['needs_user','invalid','unknown'].includes(state(t)))||tasks[0];
    $('#record-primary').disabled=!target;$('#record-primary').textContent=target?'查看裁决依据':'尚无登记任务';
    renderIssueCards();renderReleases();renderEvidence(target);
    $('#record-notice').innerHTML=`<div><strong>只读记录快照 · ${esc(snapshot.project_path)}</strong><span>导出于 ${esc(snapshot.generated_at.replace('T',' '))} · 非实时连接</span></div><p>展示已有文件记录；未运行 gate，未重新核对当前源码。${snapshot.notes.length?`<b>读取有 ${snapshot.notes.length} 条提示，记录可能不完整。</b>`:''}</p>${snapshot.notes.length?`<details><summary>查看读取提示</summary><pre>${esc(snapshot.notes.join('\n'))}</pre></details>`:''}`;
    $('.footer>span').innerHTML=icon('shield')+'只读快照 · 审查通过不等于放行 · 追加轮次由用户批准 · 外部故障（容量/断网/额度）不计审查结论';
  }
  function renderEvidence(t) {
    const r=t&&latest(t),g=r?.gate;
    $('.evidence-strip').innerHTML=`<div class="evidence-info"><div class="strip-heading"><strong>最近保存的裁决</strong><span class="mono">${esc(t?.id||'—')} ${r?' / R'+String(r.number).padStart(2,'0'):''}</span></div><div class="version-key">${icon('lock')}${esc(r?.fingerprint? r.fingerprint.slice(0,16)+'…'+r.fingerprint.slice(-8):'尚无冻结指纹')}</div><div class="evidence-links">${r?`${r.file_count??'未知'} 个文件 · ${r.verification.length} 项必做验证记录 · 未重跑`:'尚未送审'}</div></div><div class="evidence-log"><div class="log-row"><strong>${t?status(state(t)):'暂无任务'}</strong><span class="log-path">${esc(g?.at||'未保存 gate.json')}</span></div><div class="log-row"><strong>${g?.rejects?.length?'退回审查员：'+esc(g.rejects.join('、')):'以所选轮次的 gate.json 为准'}</strong></div><div class="log-row"><strong>原始记录与当前文件的一致性，由调度器重新核对</strong></div></div>`;
  }
  function renderIssueCards() {
    const issues=allIssues(),count=s=>issues.filter(i=>i.status===s).length;
    $('#issues-view .section-label').innerHTML=`<div class="issue-filters" aria-label="问题状态筛选">${[['all','全部',issues.length],['open','未关闭',count('open')],['closing','关闭中',count('closing')],['closed','已关闭',count('closed')]].map(([key,title,n])=>`<button class="${issueFilter===key?'active':''}" data-record-issue-filter="${key}" aria-pressed="${issueFilter===key}">${title} ${n}</button>`).join('')}</div><span class="mono">按稳定 ID 保留，可能包含重复追踪</span>`;
    const list=issues.filter(i=>issueFilter==='all'||i.status===issueFilter);
    $('#issues-grid').innerHTML=list.length?list.map(i=>`<article class="issue-card"><span class="chip ${i.status==='open'?'coral':i.status==='closed'?'mint':''}">${esc(issueLabels[i.status]||'未知状态')}</span><h3>${esc(i.summary||i.text?.actual||i.text?.where||i.id)}</h3><span class="issue-id">${esc(i.id)} · ${esc(i.task)}</span><p class="record-issue-summary">位置：${esc(i.text?.where||'未记录')}</p><div class="record-issue-meta">提出者 ${esc(i.raised_by)} · R${String(i.round).padStart(2,'0')} · ${i.history?.length||0} 条历史</div><button class="btn" data-record-issue="${esc(i.task+'|'+i.id)}">查看依据与处理历史 ${icon('arrow')}</button></article>`).join(''):'<div class="empty-column">此状态没有问题记录</div>';
  }
  function renderReleases() {
    const releases=snapshot.tasks.flatMap(t=>t.rounds.filter(r=>r.release).map(r=>({t,r})));
    $('#ledger-view .section-label').innerHTML=`<span>历史放行提交记录 · 当前有效性未复核</span><span class="mono">${releases.length} RECORDS</span>`;
    $('#ledger-list').innerHTML=releases.length?releases.map(({t,r})=>`<article class="ledger-entry"><time>${esc(r.release.at||'未知时间')}</time><div><h3>${esc(t.name)} / R${String(r.number).padStart(2,'0')}</h3><p>来源 released.json；不代表当前版本仍可放行。</p><button class="btn" data-record-task="${esc(t.id)}" data-record-round="${r.number}">查看原裁决与证据</button></div></article>`).join(''):'<div class="empty-column">尚无 released.json 放行提交记录。审查员通过、测试通过，均不能代替 gate 放行。</div>';
  }
  function openTask(id,roundNo) {
    selectedTask=getTask(id);if(!selectedTask)return;
    selectedRound=roundNo||latest(selectedTask)?.number;tab=selectedTask.review==='draft'?'log':'gate';attempts={};dialogKind='record';renderDialog();openDialog();
  }
  function draftDialog(t) {
    $('#dialog-eyebrow').textContent=`${t.id} / 设计阶段 / 公示`;
    $('#dialog-title').textContent=t.name;$('#dialog-status').innerHTML=status('published');
    $('#dialog-action').textContent='关闭';$('#dialog-action').className='btn';
    const tabs=[['log','修订记录'],['req','需求']].concat(t.files.flatMap((f,i)=>[['rev'+i,f.label+'（修订版）'],['orig'+i,f.label+'（'+t.author+' 原稿）']]));
    if(!tabs.some(([k])=>k===tab))tab='log';
    let html=`<div class="record-round-bar"><span>${esc(t.author)} 起草 → ${esc(t.reviser)} 修订 → 公示 · 不走审查；后续代码任务的审查以修订版为准</span></div><div class="dialog-tabs">${tabs.map(([k,title])=>`<button data-record-tab="${k}" class="${tab===k?'active':''}">${esc(title)}</button>`).join('')}</div>`;
    if(tab==='log')html+=t.revision_log?`<pre class="code">${esc(t.revision_log)}</pre>`:'<div class="record-warning">修订版缺少「## 修订记录」一节</div>';
    else if(tab==='req')html+=`<p class="mono">${esc(t.requirements_path||'')}</p><pre class="code">${esc(t.requirements||'记录缺失')}</pre>`;
    else{const i=Number(tab.slice(tab.startsWith('rev')?3:4)),f=t.files[i],rev=tab.startsWith('rev');html+=`<p class="mono">${esc(rev?f.revised_path:f.original_path)}</p><pre class="code">${esc((rev?f.revised:f.original)||'记录缺失')}</pre>`;}
    $('#dialog-body').innerHTML=html;$('.dialog-footer>span').textContent='设计阶段公示：只展示原稿、修订版与修订记录';
  }
  function renderDialog() {
    const t=selectedTask,r=getRound();
    if(t.review==='draft')return draftDialog(t);
    $('#dialog-eyebrow').textContent=`${t.id} / ${r?'ROUND '+String(r.number).padStart(2,'0'):'未开轮'} / 只读文件快照`;
    $('#dialog-title').textContent=t.name;$('#dialog-status').innerHTML=r?status(r===latest(t)?state(t):roundState(r,t.id)):status('todo');
    $('#dialog-action').textContent='关闭';$('#dialog-action').className='btn';
    let html=`<div class="record-round-bar"><label>送审轮次 <select id="record-round">${t.rounds.map(x=>`<option value="${x.number}" ${r?.number===x.number?'selected':''}>round-${String(x.number).padStart(2,'0')}</option>`).join('')}</select></label><span>当前允许 ${t.max_rounds??'未知'} 轮 · ${(r?.rules.review||t.review)==='double'?'双审':'单审'}</span></div><div class="dialog-tabs">${[['gate','裁决依据'],['rulings','用户裁决'],['reviews','审查与尝试'],['handoff','交接包'],['issues','问题与批准']].map(([key,title])=>`<button data-record-tab="${key}" class="${tab===key?'active':''}">${title}</button>`).join('')}</div>`;
    if(!r)html+='<p>配置已登记，尚未生成轮次。请通过既有流程创建交接包和 snapshot。</p>';
    else if(tab==='gate')html+=gateDetail(t,r);
    else if(tab==='reviews')html+=reviewDetail(r);
    else if(tab==='rulings')html+=rulingsDetail(t,r);
    else if(tab==='handoff')html+=`${t.summary?`<h3>一句话</h3><p>${esc(t.summary)}</p>`:''}<h3>本轮冻结的交接包</h3><pre class="code">${esc(r.handoff||'记录缺失')}</pre>`;
    else html+=`<h3>该任务的问题台账</h3><p>${t.issues.length} 条稳定 ID 记录，按原文保留；不合并或擅自关闭重复追踪。</p><ul class="record-list">${t.issues.map(i=>`<li><button class="record-text-btn" data-record-issue="${esc(t.id+'|'+i.id)}">${esc(i.id)}</button><span>${esc(issueLabels[i.status]||i.status)}</span>${i.summary?`<span>${esc(i.summary)}</span>`:''}</li>`).join('')}</ul><h3>批准记录</h3><p>只展示 baseline.json 中已有记录。此页面不执行 approve。</p>${t.approvals.length?`<pre class="code">${esc(JSON.stringify(t.approvals,null,2))}</pre>`:'<p>没有登记的批准记录。追加轮次或放宽条件，需要用户明确批准并通过 CLI 记录。</p>'}`;
    $('#dialog-body').innerHTML=html;$('.dialog-footer>span').textContent='只读记录展示，不批准、不放行、不重新运行测试';
  }
  function rulingsDetail(t,r) {
    const ext=extraRounds(t);
    const d=t.user_decision;
    return `${d?`<h3>用户最终决定</h3><div class="record-warning">第 ${esc(d.round)} 轮 · ${esc(d.decision==='accept'?'接受现状（用户裁决，不是审查放行）':d.decision)} · ${esc(d.at||'')} · ${esc(d.by||'用户')}<br>${esc(d.reason||'')}</div>`:''}<h3>用户裁决（本轮冻结交接包）</h3>${r.rulings?`<pre class="code ruling">${esc(r.rulings)}</pre>`:t.rulings?`<p>本轮交接包没有裁决段落；最近一次裁决：</p><pre class="code ruling">${esc(t.rulings)}</pre>`:'<p>尚无用户裁决记录。</p>'}<h3>项目已接受风险（冻结进之后每轮审查指令）</h3>${(snapshot.accepted_risks||[]).length?`<ul class="record-points">${snapshot.accepted_risks.map(x=>`<li><span class="mono">${esc(x.id)}</span> ${esc(x.text?.where||'')}：${esc(x.text?.actual||'')}<br>理由：${esc(x.reason||'')} · 来源 ${esc(x.source?.task||'')} ${esc(x.source?.issue_id||'')} · ${esc(x.at||'')}</li>`).join('')}</ul>`:'<p>没有已接受风险。</p>'}<h3>用户批准的追加轮次</h3>${ext.length?`<ul class="record-points">${ext.map(a=>`<li><span class="mono">${esc(a.at||'')}</span> ${esc(a.reason||'未写理由')}</li>`).join('')}</ul>`:'<p>没有追加轮次。</p>'}<h3>分工</h3><p>${esc(WORKFLOW)}</p>`;
  }
  function roundState(r,id) {
    if(r.invalid)return r.interrupted?'interrupted':'invalid';
    const g=r.gate;
    if(!g){
      const t=getTask(id);
      if(t&&latest(t)!==r){
        const rejected=Object.values(r.reviewers).some(rv=>rv.adopted&&rv.adopted.verdict==='reject');
        return rejected?'superseded_reject':'superseded';
      }
      return 'pending';
    }
    if(g.task!==id||g.round!==r.number||g.fingerprint!==r.fingerprint)return 'unknown';
    if(g.status==='released'&&(!r.release||r.release.fingerprint!==r.fingerprint))return 'unknown';
    const t2=getTask(id);
    if(g.status==='needs_user'&&t2&&accepted(t2,r))return 'user_accepted';
    return labels[g.status]?g.status:'unknown';
  }
  function gateDetail(t,r) {
    const g=r.gate, lists=[['失效原因',g?.invalid],['等待审查员',g?.pending],['尝试次数耗尽',g?.exhausted],['退回审查员',g?.rejects],['裁决时未关闭问题',g?.open_issues]];
    let html=`<div class="record-warning">这是 ${esc(g?.at||'尚未保存')} 的历史裁决。没有在本页面重算指纹、重跑测试或复核原始证据；文件当前有效性未确认。</div><h3>裁决：${esc(labels[roundState(r,t.id)])}</h3>${lists.filter(([,a])=>Array.isArray(a)&&a.length).map(([title,items])=>`<h3>${title}</h3><ul class="record-points">${items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`).join('')}<h3>版本与扫描规则</h3><pre class="code">SHA256 ${esc(r.fingerprint||'缺失')}\n文件数 ${esc(r.file_count??'未知')}\ninclude: ${esc((r.rules.scan?.include||[]).join(', '))}\nexclude: ${esc((r.rules.scan?.exclude||[]).join(', '))}\nsymlinks: ${esc(r.rules.scan?.symlinks||'未知')}</pre><h3>脚本必做验证记录</h3>`;
    html+=r.verification.length?r.verification.map(v=>{const res=v.result;return `<section class="review-result"><div class="mono">${esc(v.spec?.cmd||'缺少冻结命令')}</div><p>${res?`记录退出码 ${esc(res.exit_code)} · ${res.timed_out?'超时':'未超时'} · ${v.output===null?'缺少完整输出':'输出已读取'}`:'缺少 result.json，不可视为通过'}</p><p>必含文本：${esc(v.spec?.expect??'未设置')} · 本页面未重跑或作放行判定</p><details><summary>查看输出原文</summary><pre class="code">${esc(v.output??'缺失')}</pre></details></section>`}).join(''):'<p>本轮没有配置必做验证；这不等于验证已通过。</p>';
    html+=`<details><summary>查看 gate.json 原记录</summary><pre class="code">${esc(g?JSON.stringify(g,null,2):'尚无 gate.json')}</pre></details>`;return html;
  }
  function reviewDetail(r) {
    return Object.entries(r.reviewers).map(([by,rv])=>{
      const number=attempts[by]??rv.adopted?.attempt??rv.attempts.at(-1)?.number;
      const a=rv.attempts.find(x=>x.number===Number(number));
      if(!a)return `<section class="review-result"><h3>${esc(by)}</h3><p>尚无 attempt 记录；不能视为通过。</p></section>`;
      const m=a.meta,v=a.verdict;
      return `<section class="review-result"><div class="record-attempt-head"><strong>${esc(by)}</strong><label>尝试 <select data-record-attempt="${by}">${rv.attempts.map(x=>`<option value="${x.number}" ${x.number===a.number?'selected':''}>attempt-${String(x.number).padStart(2,'0')} ${x.adopted?'· 已采用':'· 未采用'}</option>`).join('')}</select></label></div><p>结论记录：${esc(outcomes[m?.outcome]||m?.outcome||'未完成 / 缺少 meta')} · ${a.adopted?'adopted.json 指向本次':'未被采用，不作为放行依据'}</p>${a.external?`<p class="ext-note">外部故障：${esc(a.external)}——不是审查结论，重跑即可。</p>`:''}<p>退出码 ${esc(m?.exit_code??'缺失')} · ${m?.timed_out?'超时':'未记录超时'} · 耗时 ${esc(m?.seconds??'未知')} 秒</p>${m?.errors?.length?`<pre class="code">错误：${esc(m.errors.join('\n'))}</pre>`:''}${m?.violations?.length?`<pre class="code">违规：${esc(m.violations.join('\n'))}</pre>`:''}<p>原始记录文件：${Object.entries(a.artifacts).map(([name,exists])=>`${esc(name)} ${exists?'✓':'缺失'}`).join(' · ')}。文件存在不代表已通过一致性校验。</p>${v?`<p>本次 ${v.blockers?.length??'未知'} 条 blocker · ${v.polish?.length??'未知'} 条 polish · ${v.hardening?.length??0} 条加固项 · ${v.prior_issues?.length??0} 条历史表态</p><details><summary>查看结构化结论（含历史表态、polish 和加固项）</summary><pre class="code">${esc(JSON.stringify(v,null,2))}</pre></details>`:'<p>缺少合法可读的 verdict.json；不得视为通过。</p>'}</section>`;
    }).join('');
  }
  function openIssue(key) {
    const i=allIssues().find(i=>i.task+'|'+i.id===key);if(!i)return;
    dialogKind='record';$('#dialog-eyebrow').textContent=i.task+' / '+i.id+' / 问题台账原记录';$('#dialog-title').textContent=i.summary||i.text?.where||i.id;$('#dialog-status').innerHTML=`<span class="chip">${esc(issueLabels[i.status]||i.status)}</span>`;
    $('#dialog-body').innerHTML=`<h3>位置</h3><p>${esc(i.text?.where||'未记录')}</p>${[['触发条件',i.text?.trigger],['实际结果',i.text?.actual],['违反的要求',i.text?.why],['代码 / 输出依据',i.text?.evidence]].map(([title,value])=>`<h3>${title}</h3><pre class="code">${esc(value||'未记录')}</pre>`).join('')}<h3>实现者逐轮回复</h3><pre class="code">${esc(JSON.stringify(i.replies||[],null,2))}</pre><h3>完整状态历史</h3><pre class="code">${esc(JSON.stringify(i.history||[],null,2))}</pre><p>open → closing → closed。只有本轮正式放行，closing 才算关闭；本页面不提供手动关闭操作。</p>`;
    $('#dialog-action').textContent='关闭';$('#dialog-action').className='btn';$('.dialog-footer>span').textContent='保留稳定 ID；仅展示原记录';openDialog();
  }
  function help() {
    dialogKind='record';$('#dialog-eyebrow').textContent='RECORD SNAPSHOT / READ ONLY';$('#dialog-title').textContent='这份页面如何对应实际工具';$('#dialog-status').innerHTML='<span class="chip">只读文件快照</span>';
    $('#dialog-body').innerHTML=`<ul class="help-list"><li>左上角可切换项目；清单在 projects.json，切换后只读导出该项目的记录，不影响其他项目。</li><li>分工：${esc(WORKFLOW)}。</li><li>「用户裁决」页签显示交接包里「## 用户裁决」一节和用户批准追加轮次的理由。</li><li>审查员因 OpenAI 容量不足、断网、额度用完等失败，会标为「外部故障 · 不计结论」，与退回分开；调度器中途崩溃作废的轮次标为「本轮中断」，不计入轮次配额；卡片上的「计入配额 x/y」才是和上限比较的轮数。</li><li>任务来自 crew/crew.json。实施计划中的任务不会自动变成登记任务。</li><li>轮次、采用的 attempt、审查结论、问题台账和批准记录都来自原文件；历史问题按稳定 ID 保留。</li><li>审查员通过、进程退出码 0、必做验证通过均不能独立代表放行。只展示已有 gate.json / released.json，不重新执行裁决。</li><li>快照导出于 ${esc(snapshot.generated_at)}。刷新浏览器只重读快照文件；不是实时监控。用 serve.py 启动时，每次刷新都会重新只读导出；直接打开文件时需手动运行 export_records.py。</li><li>当前源码是否与送审指纹一致，仍须通过原工具核对；本页面不执行 snapshot、review、gate 或 approve。</li><li>可切换到原交互演示。演示数据与文件快照分开呈现。</li></ul>`;
    $('#dialog-action').textContent='关闭';$('#dialog-action').className='btn';openDialog();
  }
  function renderProjectSwitch() {
    if(!projects.length)return;
    $('.project-switch').innerHTML=`<span class="project-icon">${esc((currentProject||'?').slice(0,2))}</span><div class="project-pick"><label for="project-select" class="sr-label">项目</label><select id="project-select" aria-label="切换项目">${projects.map(p=>`<option value="${esc(p.name)}" ${p.name===currentProject?'selected':''}>${esc(p.name)}${p.ok?'':'（无 crew.json）'}</option>`).join('')}</select><small>${projects.length} 个项目</small></div>`;
    $('.breadcrumb strong').textContent=currentProject||'';
  }
  async function loadProjects() {
    if(location.protocol==='file:')return;
    try {
      const res=await fetch('projects',{cache:'no-store'});if(!res.ok)return;
      projects=await res.json();
      let saved=null;try{saved=localStorage.getItem(PROJECT_KEY);}catch(e){}
      if(!currentProject||!projects.some(p=>p.name===currentProject))currentProject=projects.some(p=>p.name===saved)?saved:projects[0]?.name||null;
      renderProjectSwitch();
    } catch(e) {}
  }
  async function load() {
    if(running){toast('请先完成当前演示，再切换记录视图。');return;}
    const btn=$('#source-toggle');btn.disabled=true;
    try {
      await loadProjects();
      const res=await fetch(recordsUrl(),{cache:'no-store'});
      const data=await res.json();if(!res.ok)throw new Error(data?.error||'HTTP '+res.status);
      if(data.version!==1||data.mode!=='record_snapshot'||!Array.isArray(data.tasks)||!Array.isArray(data.notes))throw new Error('快照结构不匹配');
      clearTimeout(toastTimer);$('#toast').classList.add('hidden');closeDialog();$('#search').value='';pendingOnly=false;$('#filter').classList.remove('active');$('#filter').setAttribute('aria-pressed','false');snapshot=data;api.active=true;$('#simulate').classList.add('hidden');$('#record-primary').classList.remove('hidden');btn.textContent='切换演示';$('#export').innerHTML=icon('download')+'导出审查报告';$('.demo-tag').innerHTML='<i class="dot"></i>只读快照';$('.version-badge').textContent='READ ONLY';$('#record-notice').classList.remove('hidden');navigate('room');
    } catch(e) {$('#record-notice').innerHTML=`<strong>记录加载失败</strong><p>${esc(e.message)}。${api.active?'保留上一份快照；不是最新记录。':'当前仍为演示数据，不代表项目状态。'}</p>`;$('#record-notice').classList.remove('hidden');toast('快照读取失败；未执行任何项目操作。');}
    finally{btn.disabled=false;}
  }
  function demo() {
    clearTimeout(toastTimer);$('#toast').classList.add('hidden');tasks[4].status='review';tasks[4].note='dsh 已通过，等待 Codex';simulated=false;running=false;$('#simulate').disabled=false;$('#simulate').innerHTML=icon('play')+'开始演示双审';$('#search').value='';$('#search').placeholder='搜索任务…';pendingOnly=false;$('#filter').classList.remove('active');$('#filter').setAttribute('aria-pressed','false');api.active=false;closeDialog();$('.side-progress-head>span').textContent='项目完成度';savedSelectors.forEach(s=>$(s).innerHTML=original[s]);$('#source-toggle').textContent='查看记录快照';$('#simulate').classList.remove('hidden');$('#record-primary').classList.add('hidden');$('#export').innerHTML=icon('download')+'导出演示报告';$('.demo-tag').innerHTML='<i class="dot"></i>演示数据';$('.version-badge').textContent='CONCEPT 03';$('#record-notice').innerHTML='<div><strong>交互演示</strong><span>所有状态均为模拟数据</span></div><p>这里可以体验送审到放行，不代表真实项目进度；文件记录可随时切回。</p>';$('[data-nav="tasks"] .nav-count').textContent='7';$('[data-nav="issues"] .nav-count').textContent='2';$('.dialog-footer>span').textContent='演示预览，不调用 AI 或修改真实项目';renderIssues();navigate('room');
  }
  $('#source-toggle').addEventListener('click',()=>api.active?demo():load());
  $('#record-primary').addEventListener('click',()=>{const t=snapshot?.tasks.find(t=>['needs_user','invalid','unknown'].includes(state(t)))||snapshot?.tasks[0];if(t)openTask(t.id);});
  $('#export').addEventListener('click',e=>{
    if(!api.active)return;e.stopImmediatePropagation();if(location.protocol==='file:'){toast('导出 Word 报告需要通过 serve.py 打开本页面。');return;}const a=document.createElement('a');a.href='report.docx?project='+encodeURIComponent(currentProject||'')+'&t='+Date.now();a.download='crew-review-report.docx';a.click();toast('正在生成 Word 审查报告…');
  },true);
  document.addEventListener('click',e=>{
    if(!api.active)return;
    const task=e.target.closest('[data-record-task]');if(task)openTask(task.dataset.recordTask,Number(task.dataset.recordRound)||undefined);
    const issue=e.target.closest('[data-record-issue]');if(issue)openIssue(issue.dataset.recordIssue);
    const btn=e.target.closest('[data-record-tab]');if(btn){tab=btn.dataset.recordTab;renderDialog();$(`[data-record-tab="${tab}"]`).focus();}
    const filter=e.target.closest('[data-record-issue-filter]');if(filter){issueFilter=filter.dataset.recordIssueFilter;renderIssueCards();$(`[data-record-issue-filter="${issueFilter}"]`).focus();}
  });
  document.addEventListener('change',e=>{if(e.target.id==='project-select'){currentProject=e.target.value;try{localStorage.setItem(PROJECT_KEY,currentProject);}catch(err){}selectedTask=null;load();return;}if(!api.active)return;if(e.target.id==='record-round'){selectedRound=Number(e.target.value);attempts={};renderDialog();$('#record-round').focus();}const by=e.target.dataset.recordAttempt;if(by){attempts[by]=Number(e.target.value);renderDialog();$(`[data-record-attempt="${by}"]`).focus();}});
  document.addEventListener('keydown',e=>{if(api.active&&(e.key==='Enter'||e.key===' ')&&e.target.matches('[data-record-task][role="button"]')){e.preventDefault();openTask(e.target.dataset.recordTask,Number(e.target.dataset.recordRound)||undefined);}});
  // 只在打开页面时问一次是否在跑（run.flag）；在跑就每 5 秒刷新，跑完刷最后一次后停止。空闲时不做任何检查。
  let liveTimer=null;
  async function softRefresh() {
    try {
      const want=currentProject;
      const res=await fetch(recordsUrl(),{cache:'no-store'});if(!res.ok)return;
      const data=await res.json();if(data.version!==1||!Array.isArray(data.tasks)||want!==currentProject)return;
      snapshot=data;if(selectedTask)selectedTask=getTask(selectedTask.id)||selectedTask;
      renderRecords();
      const body=$('#dialog-body');
      if(!$('#overlay').classList.contains('hidden')&&$('#record-round')){const y=body.scrollTop;renderDialog();body.scrollTop=y;}
    } catch(e) {}
  }
  async function isRunning() {
    try{return (await (await fetch('status',{cache:'no-store'})).json()).running===true;}catch(e){return null;}  /* null = 服务暂时连不上（如重启），不当作“已结束” */
  }
  async function live() {
    liveTimer=null;
    if(document.hidden)return;
    const running=await isRunning();
    if(running===null){liveTimer=setTimeout(live,5000);return;}
    if(api.active)await softRefresh();
    if(running)liveTimer=setTimeout(live,5000);else{wasLive=false;toast('审查已结束，已刷新到最新记录。');idle();}
  }
  // 空闲时每 10 秒只问一次 /status（看 run.flag 在不在，不扫描项目）；一开始跑就进入 5 秒刷新。
  let idleTimer=null;
  function idle() {
    clearTimeout(idleTimer);
    if(location.protocol==='file:')return;
    idleTimer=setTimeout(async()=>{
      if(liveTimer!==null)return;
      if(!document.hidden&&await isRunning()===true){wasLive=true;toast('检测到审查开始，每 5 秒自动刷新。');await softRefresh();liveTimer=setTimeout(live,5000);}
      else idle();
    },10000);
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&liveTimer===null&&wasLive)live();});
  let wasLive=false;
  load().then(async()=>{if(location.protocol!=='file:'&&await isRunning()===true){wasLive=true;toast('审查运行中，每 5 秒自动刷新。');liveTimer=setTimeout(live,5000);}else idle();});
})();

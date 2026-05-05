/* ── State ── */
const S = { page:1, perPage:50, sort:'score', order:'desc', totalPages:1, tabStatus:'', role:'guest', authenticated:false, activeTab:'jobs', opsWindow:24 };
const $ = id => document.getElementById(id);

/* ── CSRF: read JS-readable cookie set by CSRFMiddleware and inject the
   X-CSRF-Token header on every unsafe (state-changing) request. We wrap the
   global fetch once so call sites stay unchanged. ── */
function _readCsrfCookie() {
  const m = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}
const _UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const _origFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const method = (init.method || (typeof input === 'string' ? 'GET' : input.method) || 'GET').toUpperCase();
  if (_UNSAFE.has(method)) {
    const token = _readCsrfCookie();
    if (token) {
      init.headers = new Headers(init.headers || {});
      if (!init.headers.has('X-CSRF-Token')) init.headers.set('X-CSRF-Token', token);
    }
  }
  return _origFetch(input, init);
};

/* ── Tabs ── */
function switchTab(name, save=true) {
  S.activeTab = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab===name));
  if(save && S.authenticated) try{ localStorage.setItem('pipka_tab', name); }catch(e){}

  if (name === 'settings') {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id==='tc-settings'));
    loadProfile();
  } else if (name === 'ops') {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id==='tc-ops'));
    loadOpsOverview();
  } else {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id==='tc-jobs'));
    S.tabStatus = name === 'jobs' ? '' : (name === 'inbox' ? 'new' : name);
    S.page = 1;
    loadJobs();
  }
}

function openJobsView({ tab='jobs', minScore=null, source=null, search='' } = {}) {
  if(minScore !== null && $('f-score')) $('f-score').value = String(minScore);
  if(source !== null && $('f-source')) $('f-source').value = source;
  if($('f-search')) $('f-search').value = search;
  switchTab(tab);
}

/* ── Filters ── */
let searchTimeout;
$('f-search').addEventListener('input', () => { clearTimeout(searchTimeout); searchTimeout=setTimeout(()=>{S.page=1;loadJobs()},400); });
['f-score','f-source'].forEach(id => $(id).addEventListener('change', ()=>{S.page=1;loadJobs()}));

/* ── Multi-select country ── */
const COUNTRY_NAMES = {
  de:'Germany', at:'Austria', ch:'Switzerland', nl:'Netherlands', be:'Belgium',
  pl:'Poland', cz:'Czech Rep', es:'Spain', it:'Italy', fr:'France',
  gb:'UK', uk:'UK', ie:'Ireland', dk:'Denmark', se:'Sweden', no:'Norway',
  fi:'Finland', ro:'Romania', hu:'Hungary', sk:'Slovakia', si:'Slovenia',
  us:'USA', ca:'Canada', pt:'Portugal', lu:'Luxembourg', ee:'Estonia',
  lv:'Latvia', lt:'Lithuania', hr:'Croatia', rs:'Serbia', bg:'Bulgaria',
};
let _selCountries = new Set();

function toggleCountryDrop(e) {
  e.stopPropagation();
  $('f-country-drop').classList.toggle('open');
}
document.addEventListener('click', (e) => {
  if (!$('country-wrap').contains(e.target)) $('f-country-drop').classList.remove('open');
});

async function loadCountries() {
  try {
    const data = await (await fetch('/api/countries',{cache:'no-store'})).json();
    $('ms-list').innerHTML = data.map(c => {
      const name = COUNTRY_NAMES[c.code] || c.code.toUpperCase();
      return `<label class="ms-item"><input type="checkbox" value="${c.code}" onchange="onCountryToggle()"><span>${name}</span><span class="ms-cnt">${c.count}</span></label>`;
    }).join('');
  } catch(e) { console.error('loadCountries:', e); }
}

function onCountryToggle() {
  _selCountries = new Set([...document.querySelectorAll('#ms-list input:checked')].map(i=>i.value));
  _updateCountryBtn();
  S.page=1; loadJobs();
}

function _updateCountryBtn() {
  const n = _selCountries.size;
  $('f-country-label').textContent = n===0 ? 'All Countries'
    : n===1 ? (COUNTRY_NAMES[[..._selCountries][0]] || [..._selCountries][0].toUpperCase())
    : `${n} countries`;
}

function msAll(e) {
  e.stopPropagation();
  document.querySelectorAll('#ms-list input').forEach(i=>i.checked=true);
  onCountryToggle();
}
function msNone(e) {
  e.stopPropagation();
  document.querySelectorAll('#ms-list input').forEach(i=>i.checked=false);
  onCountryToggle();
}
$('prev-btn').addEventListener('click', () => { if(S.page>1){S.page--;loadJobs()} });
$('next-btn').addEventListener('click', () => { if(S.page<S.totalPages){S.page++;loadJobs()} });

/* Sort headers */
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col=th.dataset.sort;
    if(S.sort===col) S.order=S.order==='desc'?'asc':'desc'; else {S.sort=col;S.order='desc';}
    S.page=1; updateSortHeaders(); loadJobs();
  });
});
function updateSortHeaders(){
  document.querySelectorAll('th[data-sort]').forEach(th=>{
    const col=th.dataset.sort, arrow=th.querySelector('.arrow');
    th.classList.toggle('sorted',col===S.sort);
    if(arrow) arrow.innerHTML=col===S.sort?(S.order==='desc'?'&#9660;':'&#9650;'):'';
  });
}

/* ── Stats ── */
async function loadStats(){
  try{
    const d=await(await fetch('/api/stats',{cache:'no-store'})).json();
    $('stat-total').textContent=d.total_jobs||0;
    $('stat-scored').textContent=d.scored||0;
    $('stat-top').textContent=d.top_matches||0;
    $('stat-applied').textContent=d.applied||0;
    $('stat-rejected').textContent=d.rejected||0;
    $('tab-inbox-count').textContent=d.inbox||0;
    $('tab-applied-count').textContent=d.applied||0;
    $('tab-rejected-count').textContent=d.rejected||0;
  }catch(e){console.error(e)}
}

/* ── Jobs table ── */
async function loadJobs(){
  const p=new URLSearchParams({page:S.page,per_page:S.perPage,sort:S.sort,order:S.order,min_score:$('f-score').value});
  const search = $('f-search').value.trim();
  if(search) p.set('search', search);
  if($('f-source').value) p.set('source',$('f-source').value);
  if(_selCountries.size>0) p.set('countries',[..._selCountries].join(','));
  if(S.tabStatus) p.set('status',S.tabStatus);
  const tbody=$('jobs-body');
  tbody.innerHTML='<tr><td colspan="6" class="loading">Loading...</td></tr>';
  try{
    const data=await(await fetch('/api/jobs?'+p,{cache:'no-store'})).json();
    S.totalPages=data.pages;
    $('page-info').textContent=`Page ${data.page} of ${data.pages} (${data.total} jobs)`;
    $('prev-btn').disabled=data.page<=1; $('next-btn').disabled=data.page>=data.pages;
    if(!data.jobs.length){tbody.innerHTML='<tr><td colspan="6" class="loading">No jobs found</td></tr>';return;}
    let rows='';
    for(let i=0;i<data.jobs.length;i++){
      try{ rows+=jobRow(data.jobs[i]); }
      catch(err){ console.error('jobRow crash at index',i,'job:',data.jobs[i],'error:',err); rows+=`<tr><td colspan="6" style="color:red">Row ${i} error: ${err.message} (job id=${data.jobs[i]?.id})</td></tr>`; }
    }
    tbody.innerHTML=rows;
  }catch(e){tbody.innerHTML=`<tr><td colspan="6" class="loading">Error: ${e.message}</td></tr>`;console.error('loadJobs error:',e)}
}

function jobRow(j){
  const sc=j.score>=70?'high':j.score>=40?'mid':j.score!=null?'low':'none';
  const date=j.posted_at?new Date(j.posted_at).toLocaleDateString('de-DE'):'';
  return `<tr>
    <td><span class="score ${sc}">${j.score!=null?j.score:'—'}</span></td>
    <td><a class="job-title" href="${safeUrl(j.url)}" target="_blank" rel="noopener noreferrer">${esc(j.title)}</a>${date?`<div class="job-meta">${date}</div>`:''}</td>
    <td class="job-meta">${esc(j.company)}</td>
    <td class="job-meta">${esc(j.location)} (${esc(j.country)})</td>
    <td><span class="source-tag">${esc(j.source)}</span></td>
    <td><div class="actions-wrap">
      ${S.authenticated ? `
      <button onclick="doAction(${j.id},'applied',this)" title="Applied"${j.status==='applied'?' class="active"':''}>&#10004;</button>
      <button onclick="doAction(${j.id},'reject',this)" title="Reject"${j.status==='rejected'?' class="active"':''}>&#10006;</button>
      <button onclick="showAnalysis(${j.id},'${esc(j.title).replace(/'/g,"\\'")}')">&#129302;</button>
      ` : `<span style="font-size:10px; color:var(--muted)">Sign in to act</span>`}
    </div></td></tr>`;
}

/* ── Settings / Profile ── */
async function loadProfile(){
  try{
    const d=await(await fetch('/api/profile',{cache:'no-store'})).json();
    if(!d.profile) return;
    const p=d.profile;
    $('s-resume').value=p.resume_text||'';
    $('s-titles').value=(p.target_titles||[]).join(', ');
    if($('s-excluded')) $('s-excluded').value=(p.excluded_keywords||[]).join(', ');
    if($('s-english-only')) $('s-english-only').checked=!!p.english_only;
    $('s-salary').value=p.min_salary||'';
    $('s-experience').value=p.experience_years||'';
    $('s-languages').value=p.languages?Object.entries(p.languages).map(([k,v])=>k+':'+v).join(', '):'';
    $('s-workmode').value=p.work_mode||'any';
    const pc = p.preferred_countries||[];
    document.querySelectorAll('#s-countries-grid .country-item').forEach(el => {
      el.classList.toggle('active', pc.includes(el.dataset.code));
    });
  }catch(e){console.error(e)}
}

async function saveProfile(){
  const fd=new FormData();
  fd.append('resume_text',$('s-resume').value);
  fd.append('target_titles',$('s-titles').value);
  if($('s-excluded')) fd.append('excluded_keywords',$('s-excluded').value);
  fd.append('english_only', $('s-english-only') && $('s-english-only').checked ? '1' : '0');
  fd.append('min_salary',$('s-salary').value||0);
  fd.append('experience_years',$('s-experience').value||0);
  fd.append('languages',$('s-languages').value);
  fd.append('work_mode',$('s-workmode').value);
  const activeCountries = Array.from(document.querySelectorAll('#s-countries-grid .country-item.active')).map(el => el.dataset.code);
  fd.append('preferred_countries', activeCountries.join(','));
  try{
    const r=await fetch('/api/profile',{method:'POST',body:fd});
    const d=await r.json();
    $('settings-msg').innerHTML=d.ok?'<div class="msg ok">Settings saved!</div>':'<div class="msg err">Error saving</div>';
    setTimeout(()=>$('settings-msg').innerHTML='',3000);
  }catch(e){$('settings-msg').innerHTML='<div class="msg err">Network error</div>';}
}

/* Resume upload */
$('resume-file').addEventListener('change', async(e)=>{
  const file=e.target.files[0]; if(!file) return;
  $('upload-zone').textContent='Uploading '+file.name+'...';
  const fd=new FormData(); fd.append('file',file);
  try{
    const r=await fetch('/api/profile/resume',{method:'POST',body:fd});
    const d=await r.json();
    if(d.ok){
      $('upload-zone').textContent='Uploaded! '+d.length+' chars extracted';
      $('s-resume').value=d.preview+(d.length>500?'...(truncated in preview)':'');
      loadProfile();
    } else {
      $('upload-zone').textContent='Error: '+(d.error||'unknown');
    }
  }catch(e){$('upload-zone').textContent='Upload failed';}
});

/* ── Actions & Modal ── */
async function doAction(jobId,action,btn){
  btn.disabled=true;
  try{
    const resp = await fetch(`/api/jobs/${jobId}/action?action=${action}`,{method:'POST'});
    if(resp.status===401){
      // Session expired — reload page to re-authenticate
      alert('Session expired. Page will reload to re-authenticate.');
      window.location.reload();
      return;
    }
    if(!resp.ok){
      const body = await resp.json().catch(()=>({}));
      console.error('Action failed',resp.status,body);
      btn.disabled=false;
      return;
    }
    loadJobs(); loadStats();
  }catch(e){console.error('doAction error:',e)}
  btn.disabled=false;
}

async function showAnalysis(jobId,title){
  $('modal').classList.add('open');
  $('modal-title').textContent='AI Analysis: '+title;
  $('modal-body').textContent='Loading...';
  try{
    const d=await(await fetch(`/api/jobs/${jobId}/analyze`,{cache:'no-store'})).json();
    $('modal-body').textContent=d.analysis||d.error||'No analysis';
  }catch(e){$('modal-body').textContent='Error: '+e.message;}
}
function closeModal(){$('modal').classList.remove('open')}
$('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal()});


function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function safeUrl(u){if(!u)return'#';try{const p=new URL(u);if(p.protocol==='https:'||p.protocol==='http:')return u;}catch(e){}return'#';}
function jsq(s){return String(s ?? '').replace(/\\/g,'\\\\').replace(/'/g,"\\'");}

/* Interactivity */
document.querySelectorAll('.country-item').forEach(el => {
  el.addEventListener('click', () => el.classList.toggle('active'));
});

/* Scan */
async function triggerScan(){
  const btn=$('scan-btn'); btn.disabled=true; btn.textContent='Scanning...';
  $('scan-info').textContent='Scan started, may take 2-5 min...';
  try{
    const r=await fetch('/api/scan',{method:'POST'});
    const d=await r.json();
    if(d.status==='started'){
      // Poll for new results
      let checks=0;
      const poll=setInterval(async()=>{
        checks++;
        const s=await(await fetch('/api/stats',{cache:'no-store'})).json();
        $('scan-info').textContent=`Scanning... ${s.total_jobs} jobs (${checks*10}s)`;
        $('stat-total').textContent=s.total_jobs;
        $('stat-scored').textContent=s.scored;
        $('stat-top').textContent=s.top_matches;
        if(S.activeTab === 'ops') loadOpsOverview(true);
        if(checks>=30){clearInterval(poll);btn.disabled=false;btn.textContent='Scan Now';$('scan-info').textContent='Done';loadJobs();loadStats();if(S.activeTab === 'ops') loadOpsOverview(true);}
      },10000);
    } else {
      $('scan-info').textContent=d.error||d.status;
      btn.disabled=false; btn.textContent='Scan Now';
    }
  }catch(e){$('scan-info').textContent='Error';btn.disabled=false;btn.textContent='Scan Now';}
}

async function loadScanStatus(){
  try{
    const d=await(await fetch('/api/scan/status',{cache:'no-store'})).json();
    if(d.running){
      $('scan-info').textContent='Scan running now';
      return;
    }
    if(d.next_run){
      const t=new Date(d.next_run);
      $('scan-info').textContent='Next: '+t.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
    }
  }catch(e){}
}

function fmtNumber(v){
  return (v ?? 0).toLocaleString('de-DE');
}

function fmtRelative(dateStr){
  if(!dateStr) return '—';
  const d = new Date(dateStr);
  const diff = Date.now() - d.getTime();
  const mins = Math.round(diff / 60000);
  if(mins < 1) return 'just now';
  if(mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if(hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function fmtDateTime(dateStr){
  if(!dateStr) return '—';
  return new Date(dateStr).toLocaleString('de-DE', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function setOpsWindow(hours){
  S.opsWindow = hours;
  document.querySelectorAll('[data-ops-window]').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.opsWindow) === hours);
  });
  $('ops-window-label').textContent = hours >= 168 ? 'Last 7d' : `Last ${hours}h`;
  loadOpsOverview();
}

function renderPipelineRow(label, value, max){
  const width = max ? Math.max(8, Math.round((value / max) * 100)) : 8;
  return `<div class="pipeline-row">
    <div class="label">${label}</div>
    <div class="bar"><div class="fill" style="width:${width}%"></div></div>
    <div class="count">${fmtNumber(value)}</div>
  </div>`;
}

function eventPill(status){
  const cls = status === 'success' ? 'success' : status === 'error' ? 'error' : (status === 'running' ? 'running' : 'warning');
  return `<span class="pill ${cls}">${esc(status)}</span>`;
}

function renderOpsAlerts(d){
  const alerts = [];
  const queueRisk = d.kpis.pending_pct || 0;
  const apiErrors = (d.kpis.api_401 || 0) + (d.kpis.api_500 || 0);
  const lastScanAgeHours = d.scan.last_at ? ((Date.now() - new Date(d.scan.last_at).getTime()) / 3600000) : null;

  if(queueRisk >= 35 || d.kpis.unscored_total >= 150){
    alerts.push(`
      <div class="ops-alert queue">
        <strong>Queue pressure is building</strong>
        <p>${fmtNumber(d.kpis.unscored_total)} jobs are still unscored (${queueRisk}%). This usually means scoring throughput is lagging behind collection.</p>
        <button type="button" onclick="openJobsView({ tab:'jobs', minScore:0 })">Open all jobs</button>
      </div>
    `);
  }

  if(apiErrors > 0){
    alerts.push(`
      <div class="ops-alert errors">
        <strong>API warnings detected</strong>
        <p>In the selected window there were ${fmtNumber(d.kpis.api_401)} auth issues and ${fmtNumber(d.kpis.api_500)} server-side failures. Worth checking recent events before users feel it.</p>
        <button type="button" onclick="loadOpsOverview(true)">Refresh events</button>
      </div>
    `);
  }

  if(!d.scan.running && lastScanAgeHours !== null && lastScanAgeHours > 6){
    alerts.push(`
      <div class="ops-alert queue">
        <strong>Last scan feels stale</strong>
        <p>The last successful scan was ${fmtRelative(d.scan.last_at)}. If this is unexpected, trigger a manual scan and verify the scheduler is still healthy.</p>
        <button type="button" onclick="triggerScan()">Run scan now</button>
      </div>
    `);
  }

  if(!alerts.length){
    alerts.push(`
      <div class="ops-alert success">
        <strong>System looks calm</strong>
        <p>No obvious pressure signals right now: queue is manageable, recent scans exist, and no API errors crossed the threshold for this window.</p>
        <button type="button" onclick="openJobsView({ tab:'jobs', minScore:70 })">View top matches</button>
      </div>
    `);
  }

  $('ops-alerts').innerHTML = alerts.join('');
}

function handleOpsCardAction(action){
  if(action === 'jobs_recent') openJobsView({ tab:'jobs', minScore:0 });
  else if(action === 'coverage') openJobsView({ tab:'inbox', minScore:0 });
  else if(action === 'top_matches') openJobsView({ tab:'jobs', minScore:70 });
  else if(action === 'queue') openJobsView({ tab:'jobs', minScore:0 });
  else if(action === 'api') loadOpsOverview(true);
}

async function loadOpsOverview(force=false){
  if(S.role !== 'admin') return;
  const kpis = $('ops-kpis');
  const alerts = $('ops-alerts');
  const pipeline = $('ops-pipeline');
  const sources = $('ops-sources');
  const events = $('ops-events');
  const scan = $('ops-scan');

  if(!force && S.activeTab !== 'ops') return;

  kpis.innerHTML = '<div class="ops-card"><div class="kicker">Loading</div><div class="value">...</div><div class="sub">Refreshing metrics</div></div>';
  alerts.innerHTML = '<div class="ops-alert"><strong>Loading alerts</strong><p>Checking queue pressure, API health and scan freshness.</p></div>';
  pipeline.innerHTML = '<div class="ops-empty">Loading pipeline...</div>';
  sources.innerHTML = '<div class="ops-empty">Loading sources...</div>';
  events.innerHTML = '<div class="ops-empty">Loading events...</div>';
  scan.innerHTML = '<div class="ops-empty">Loading scan health...</div>';

  try {
    const d = await (await fetch(`/api/ops/overview?window_hours=${S.opsWindow}`)).json();
    if(d.error){
      throw new Error(d.error);
    }

    const scanPayload = d.scan.last_payload || {};
    const aggregator = scanPayload.aggregator || {};
    const sourceStats = aggregator.sources || [];

    kpis.innerHTML = [
      { kicker:'Jobs In DB', value:fmtNumber(d.kpis.total_jobs), sub:`+${fmtNumber(d.kpis.jobs_recent)} in selected window`, delta:`${fmtNumber(d.kpis.active_sources)} sources active`, action:'jobs_recent' },
      { kicker:'Score Coverage', value:`${d.kpis.coverage_pct}%`, sub:`${fmtNumber(d.kpis.scored_total)} scored total`, delta:`${fmtNumber(d.kpis.unscored_total)} pending`, action:'coverage' },
      { kicker:'Top Matches', value:fmtNumber(d.kpis.top_matches), sub:`${fmtNumber(d.kpis.top_recent)} new top matches`, delta:d.kpis.avg_score_recent != null ? `avg ${d.kpis.avg_score_recent}` : 'avg —', action:'top_matches' },
      { kicker:'Queue Risk', value:`${d.kpis.pending_pct}%`, sub:'Unscored jobs still waiting', delta:`${fmtNumber(d.kpis.unscored_total)} jobs`, action:'queue' },
      { kicker:'API Warnings', value:fmtNumber(d.kpis.api_401 + d.kpis.api_500), sub:`${fmtNumber(d.kpis.api_401)} auth · ${fmtNumber(d.kpis.api_500)} server`, delta:'Latest response issues', action:'api' },
    ].map(card => `
      <div class="ops-card clickable" onclick="handleOpsCardAction('${card.action}')">
        <div class="kicker">${card.kicker}</div>
        <div class="value">${card.value}</div>
        <div class="sub">${card.sub}</div>
        <div class="delta">${card.delta}</div>
      </div>
    `).join('');
    renderOpsAlerts(d);

    const pipelineMax = Math.max(...Object.values(d.pipeline), 1);
    pipeline.innerHTML = Object.entries(d.pipeline)
      .map(([label, value]) => renderPipelineRow(label.replace('_', ' '), value, pipelineMax))
      .join('');

    const scanStatus = d.scan.running ? 'running' : (d.scan.last_status || 'unknown');
    $('ops-scan-pill').outerHTML = `<span id="ops-scan-pill" class="pill ${scanStatus === 'success' ? 'success' : scanStatus === 'error' ? 'error' : scanStatus === 'running' ? 'running' : 'warning'}">${scanStatus}</span>`;
    scan.innerHTML = `
      <div class="scan-grid">
        <div class="scan-stat"><span class="label">Last Scan</span><span class="value">${fmtRelative(d.scan.last_at)}</span></div>
        <div class="scan-stat"><span class="label">Next Run</span><span class="value">${d.scan.running ? 'Running' : fmtDateTime(d.scan.next_run)}</span></div>
        <div class="scan-stat"><span class="label">Duration</span><span class="value">${scanPayload.duration_seconds != null ? `${scanPayload.duration_seconds}s` : '—'}</span></div>
        <div class="scan-stat"><span class="label">Filtered Jobs</span><span class="value">${fmtNumber(aggregator.filtered_count || 0)}</span></div>
      </div>
      <div class="scan-list">
        <div><strong>${esc(d.scan.last_message || 'No scans recorded yet')}</strong></div>
        <div class="mini-list">
          <span>raw ${fmtNumber(aggregator.raw_count || 0)}</span>
          <span>unique ${fmtNumber(aggregator.unique_count || 0)}</span>
          <span>negative ${fmtNumber(aggregator.rejected_negative || 0)}</span>
          <span>old ${fmtNumber(aggregator.rejected_old || 0)}</span>
          <span>location ${fmtNumber(aggregator.rejected_location || 0)}</span>
          <span>queries ${fmtNumber(scanPayload.query_count || 0)}</span>
          <span>countries ${fmtNumber(scanPayload.country_count || 0)}</span>
        </div>
        ${sourceStats.length ? `<div class="mini-list">${sourceStats.map(s => `<span>${esc(s.source)}: ${fmtNumber(s.raw_count || 0)}${s.status === 'error' ? ' error' : ''}</span>`).join('')}</div>` : ''}
      </div>
    `;

    sources.innerHTML = d.sources.length
      ? d.sources.map(s => `
          <div class="source-item" style="cursor:pointer" onclick="openJobsView({ tab:'jobs', minScore:0, source:'${jsq(s.name)}' })">
            <div>
              <div class="name">${esc(s.name)}</div>
              <div class="meta">${fmtNumber(s.recent)} fresh / ${fmtNumber(s.total)} total</div>
            </div>
            <div class="source-bar"><div class="fill" style="width:${Math.max(s.fresh_share, 6)}%"></div></div>
            <div class="meta">${s.fresh_share}%</div>
          </div>
        `).join('')
      : '<div class="ops-empty">No source data yet.</div>';

    events.innerHTML = d.events.length
      ? d.events.map(ev => `
          <div class="event-item">
            <div>${eventPill(ev.status)}</div>
            <div class="summary">
              <strong>${esc(ev.type)}${ev.source ? ` · ${esc(ev.source)}` : ''}</strong>
              <p>${esc(ev.message || 'No details')}</p>
            </div>
            <div class="time">${fmtRelative(ev.at)}</div>
          </div>
        `).join('')
      : '<div class="ops-empty">No events recorded yet.</div>';
  } catch (e) {
    kpis.innerHTML = `<div class="ops-card"><div class="kicker">Ops Unavailable</div><div class="value">!</div><div class="sub">${esc(e.message || 'Unknown error')}</div></div>`;
    alerts.innerHTML = '<div class="ops-alert errors"><strong>Ops view failed</strong><p>Dashboard could not load the operational overview. Check API auth and the new ops event table.</p></div>';
    pipeline.innerHTML = '<div class="ops-empty">Could not load pipeline metrics.</div>';
    sources.innerHTML = '<div class="ops-empty">Could not load source metrics.</div>';
    events.innerHTML = '<div class="ops-empty">Could not load events.</div>';
    scan.innerHTML = '<div class="ops-empty">Could not load scan status.</div>';
  }
}

function toggleLogin() {
    if(!S.authenticated) {
        window.location.href = '/auth/google/login';
    } else {
        if(confirm('Log out?')) {
          // POST so CSRFMiddleware validates the token. The fetch wrapper
          // injects X-CSRF-Token automatically. Navigate after success so a
          // failed logout doesn't drop the user on a logged-out shell.
          fetch('/auth/logout', { method: 'POST' }).finally(() => { window.location.href = '/'; });
        }
    }
}

/* Init */
async function initApp() {
  try {
    const me = await (await fetch('/api/me',{cache:'no-store'})).json();
    S.role = me.role || 'guest';
    S.authenticated = !!me.authenticated;
    const rb = $('role-badge');
    const avatar = $('user-avatar');
    const uname = $('user-name');
    const scanBtn = $('scan-btn');
    const opsTab = $('tab-ops');

    if (!S.authenticated) {
      // Guest — show login button, hide auth-only elements, reset filters to defaults
      if (rb) { rb.textContent = 'Sign in with Google'; rb.style.background = '#4285f4'; rb.style.color = '#fff'; }
      if (scanBtn) scanBtn.style.display = 'none';
      if (opsTab) opsTab.style.display = 'none';
      // Show all jobs unfiltered for guests
      $('f-score').value = '0';
    } else {
      // Logged in — show all auth-only tabs/stats
      document.querySelectorAll('.auth-only').forEach(el => el.style.display = '');
      // Restore default score filter for logged-in users
      $('f-score').value = '70';

      if (me.avatar) { avatar.src = me.avatar; avatar.style.display = 'inline'; }
      if (me.name) { uname.textContent = me.name; uname.style.display = 'inline'; }

      if (S.role === 'admin') {
        if (rb) { rb.textContent = 'Admin'; rb.style.background = 'var(--green)'; rb.style.color = '#fff'; }
        if (scanBtn) scanBtn.style.display = 'inline-block';
        if (opsTab) opsTab.style.display = '';
      } else {
        if (rb) { rb.textContent = me.name || 'Logout'; rb.style.background = 'var(--badge)'; rb.style.color = 'var(--fg)'; }
        if (scanBtn) scanBtn.style.display = 'none';
        if (opsTab) opsTab.style.display = 'none';
      }
    }
  } catch(e) { console.error('initApp error:', e); }

  // Restore last active tab (only for authenticated users)
  if (S.authenticated) {
    try {
      const saved = localStorage.getItem('pipka_tab');
      if (saved && saved !== 'jobs') { switchTab(saved, false); }
    } catch(e) {}
  }

  loadCountries(); loadStats(); loadJobs(); loadScanStatus();
  setInterval(()=>{
    loadStats();
    loadScanStatus();
    if(S.activeTab === 'ops' && S.role === 'admin') loadOpsOverview(true);
    else if(S.activeTab !== 'settings') loadJobs();
  },300000);
}
initApp();

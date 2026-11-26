// Pipeline page: listens to /api/progress/stream and redraws the blocks.
const $ = id => document.getElementById(id);
const ICONS = { pending: '·', running: '▶', done: '✔', failed: '✖', skipped: '–' };
const KIND_NAMES = { ats: 'Company ATS boards', aggregator: 'Aggregators', site: 'Sites without an ATS', career_page: 'Career pages' };
const COUNTER_NAMES = {
  fetched: 'vacancies collected', matched: 'passed filters', new: 'in the report', telegram: 'from Telegram',
  rejected: 'rejected with a reason', new_today: 'new today', companies: 'companies in applications',
  unknown_letters: 'letters without a company',
};
const STATUS_TEXT = { running: 'running', done: 'done', failed: 'failed', interrupted: 'interrupted', stopped: 'stopped' };
let feedKind = 'all';
document.querySelectorAll('.feed-filter button').forEach(b => { b.dataset.label = b.textContent; });
let last = null;

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function dur(sec) {
  if (sec == null) return '';
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m}:${String(s).padStart(2, '0')}`;
}
const spent = s => s.started ? ((s.finished ? new Date(s.finished) : new Date()) - new Date(s.started)) / 1000 : null;

function render(d) {
  last = d;
  if (!d) {
    $('headline').textContent = 'No runs yet';
    $('subline').textContent = 'Click "Start run".';
    return;
  }
  const running = d.status === 'running';
  document.body.classList.toggle('is-running', running);
  document.querySelectorAll('[data-run]').forEach(b => b.disabled = d.busy);
  const stop = $('stop-btn');
  if (stop.hidden === !!d.busy) { stop.hidden = !d.busy; stop.disabled = false; stop.textContent = '⏹ Stop run'; }
  $('script').textContent = `${d.script}.py · ${STATUS_TEXT[d.status] || d.status}`;
  const stage = d.stages.find(s => s.key === d.stage);
  $('headline').textContent = running && stage ? stage.title
    : d.status === 'done' ? 'Run finished' : d.status === 'failed' ? 'Run failed' : d.status === 'stopped' ? 'Run stopped' : 'Run interrupted';
  const queue = d.queue && d.queue.length > 1 ? ` · next: ${d.queue.slice(1).join(', ')}` : '';
  $('subline').textContent = `started ${d.started.slice(11, 16)} · elapsed ${dur(d.elapsed)}${queue}`;

  // overall progress: by time if there is a run history, otherwise by the number of stages
  const doneStages = d.stages.filter(s => ['done', 'skipped'].includes(s.status)).length;
  let pct = d.stages.length ? 100 * doneStages / d.stages.length : 0;
  if (running && d.eta_total != null) pct = 100 * d.elapsed / (d.elapsed + d.eta_total);
  if (!running) pct = d.status === 'done' ? 100 : pct;
  $('overall-fill').style.width = pct.toFixed(1) + '%';
  $('overall-pct').textContent = `${Math.round(pct)}% · stage ${Math.min(doneStages + (running ? 1 : 0), d.stages.length)} of ${d.stages.length}`;
  $('overall-eta').textContent = running
    ? (d.eta_total != null ? `until the end ≈ ${dur(d.eta_total)}` : 'the estimate appears after the first full run')
    : `took ${dur(d.elapsed)}`;

  $('stages').innerHTML = d.stages.map(s => {
    const p = s.total ? 100 * s.done / s.total : (s.status === 'done' ? 100 : 0);
    const t = spent(s);
    return `<li class="st ${s.status}">
      <span class="ic">${ICONS[s.status]}</span>
      <div class="st-body">
        <div class="st-title">${esc(s.title)}${s.note ? ` <span class="muted small">— ${esc(s.note)}</span>` : ''}</div>
        ${s.total || s.status === 'running' ? `<div class="st-bar"><i style="width:${p}%"></i></div>` : ''}
        <div class="st-meta">${s.total ? `${s.done} / ${s.total}` : ''}${t != null ? ` · ${dur(t)}` : ''}</div>
      </div></li>`;
  }).join('');

  // "now"
  if (running && stage) {
    $('now-title').textContent = stage.title;
    const p = stage.total ? 100 * stage.done / stage.total : null;
    $('now-fill').style.width = (p ?? 0) + '%';
    $('now-fill').parentElement.classList.toggle('indeterminate', p == null);
    $('now-count').textContent = p != null
      ? `${stage.done} / ${stage.total} · ${Math.floor(p)}%${d.eta_stage != null ? ` · ≈ ${dur(d.eta_stage)}` : ''}`
      : (stage.done ? `${stage.done}` : '');
    $('now-item').innerHTML = d.last_item ? `processing: <b>${esc(d.last_item)}</b>` : '';
    $('active').innerHTML = (d.active || []).map(a =>
      `<span class="chip"><i class="pulse"></i>${esc(a.item)} <em>${a.seconds} s</em></span>`).join('');
    const skip = $('skip-btn');
    skip.hidden = !stage.skippable;
    if (skip.dataset.stage !== stage.key) { skip.disabled = false; skip.textContent = '⏭ Skip stage'; }
    skip.dataset.stage = stage.key;
  } else {
    $('skip-btn').hidden = true;
    $('now-title').textContent = running ? '—' : 'Nothing is running';
    $('now-fill').style.width = '0%';
    $('now-fill').parentElement.classList.remove('indeterminate');
    $('now-count').textContent = '';
    $('now-item').textContent = '';
    $('active').innerHTML = '';
  }

  // an even grid of tiles: at most 3 columns, rows filled as evenly as possible (6 -> 3×2, 4 -> 2×2)
  const nTiles = Object.keys(d.counters || {}).length;
  $('tiles').style.setProperty('--n', Math.max(1, Math.ceil(nTiles / Math.ceil(nTiles / 3 || 1))));
  $('tiles').innerHTML = Object.entries(d.counters || {}).map(([k, v]) =>
    `<div class="tile"><b>${Number(v).toLocaleString('en')}</b><span title="${esc(COUNTER_NAMES[k] || k)}">${esc(COUNTER_NAMES[k] || k)}</span></div>`).join('');

  // "passed" is not a reject reason (the number passed is in the "passed filters" tile), but it counts in "of N checked"
  const { 'passed': _passed, ...reasons } = (d.tallies || {}).rejects || {};
  bars('rejects', reasons, k => k, () => '');
  const rejects = Object.values((d.tallies || {}).rejects || {}).reduce((a, b) => a + b, 0);
  $('rejects-total').textContent = rejects ? `of ${rejects.toLocaleString('en')} checked` : '';
  bars('kinds', (d.tallies || {}).source_kinds, k => KIND_NAMES[k] || k, () => 'blue');

  renderFeed();
  // all warnings of the current run by group (progress.error_group), newest first
  const errs = d.errors || [];
  $('errors-box').hidden = !errs.length;
  document.querySelectorAll('#errors-box .err-group').forEach(box => {
    const items = errs.filter(e => (e.group || 'site') === box.dataset.group);
    box.hidden = !items.length;
    box.querySelector('[data-count]').textContent = items.length || '';
    box.querySelector('.errors').innerHTML = items.slice().reverse().map(e =>
      `<li><time>${e.time.slice(11, 19)}</time> <b>${esc(e.level)}</b> ${esc(e.message)}` +
      (e.trace ? `<details class="trace"><summary>details</summary><pre>${esc(e.trace)}</pre></details>` : '') +
      '</li>').join('');
  });
}

function bars(id, tally, label, cls) {
  const rows = Object.entries(tally || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(r => r[1]));
  $(id).innerHTML = rows.length ? rows.map(([k, v]) => `
    <div class="hbar ${cls(k)}"><span class="hb-label">${esc(label(k))}</span>
      <span class="hb-track"><i style="width:${Math.max(1, 100 * v / max)}%"></i></span>
      <span class="hb-val">${v.toLocaleString('en')}</span></div>`).join('')
    : '<div class="muted small">appears during a run</div>';
}

let feedShown = '';
function renderFeed() {
  // the whole run feed from all stages; redrawn only when something changed
  const all = (last && last.events) || [];
  const key = `${feedKind}:${all.length}:${all.length ? all[all.length - 1].time : ''}`;
  if (key === feedShown) return;
  feedShown = key;
  document.querySelectorAll('.feed-filter button').forEach(b => {
    const n = b.dataset.kind === 'all' ? all.length : all.filter(e => e.kind === b.dataset.kind).length;
    b.textContent = `${b.dataset.label} ${n || ''}`.trim();
  });
  const events = all.filter(e => feedKind === 'all' || e.kind === feedKind).reverse();
  $('feed').innerHTML = events.length ? events.map(e =>
    `<li class="ev ${e.kind}"><time>${e.time.slice(11, 19)}</time><span>${esc(e.text)}</span></li>`).join('')
    : '<li class="muted small">no events yet</li>';
}

document.querySelectorAll('.feed-filter button').forEach(b => b.addEventListener('click', () => {
  feedKind = b.dataset.kind;
  document.querySelectorAll('.feed-filter button').forEach(x => x.classList.toggle('on', x === b));
  renderFeed();
}));

const SKIP_KEY = 'jobhunter.skip';
const skipBoxes = [...document.querySelectorAll('.skip-opts input')];
try { // the "no …" checkboxes are remembered in the browser between visits
  const saved = JSON.parse(localStorage.getItem(SKIP_KEY) || '[]');
  skipBoxes.forEach(c => c.checked = saved.includes(c.value));
} catch (e) { /* storage unavailable — start with empty checkboxes */ }
skipBoxes.forEach(c => c.addEventListener('change', () => {
  try { localStorage.setItem(SKIP_KEY, JSON.stringify(skipBoxes.filter(x => x.checked).map(x => x.value))); } catch (e) { }
}));

// each button has its own fitting checkboxes (data-skips): "Mail only" takes none, "Vacancies only" — all but mail;
// on hover over a button the checkboxes that do not fit fade out
const allowed = b => (b.dataset.skips || '').split(' ').filter(Boolean);
document.querySelectorAll('[data-run]').forEach(b => {
  const dim = on => skipBoxes.forEach(c => c.parentElement.classList.toggle('off', on && !allowed(b).includes(c.value)));
  ['mouseenter', 'focus'].forEach(ev => b.addEventListener(ev, () => dim(true)));
  ['mouseleave', 'blur'].forEach(ev => b.addEventListener(ev, () => dim(false)));
});
document.querySelectorAll('[data-run]').forEach(b => b.addEventListener('click', async () => {
  b.disabled = true;
  const skip = skipBoxes.filter(c => c.checked && allowed(b).includes(c.value)).map(c => c.value);
  const r = await fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: b.dataset.run, skip }) });
  if (!r.ok) { alert((await r.json()).detail || 'Could not start'); b.disabled = false; }
}));

$('skip-btn').addEventListener('click', async e => {
  const b = e.currentTarget;
  b.disabled = true;
  b.textContent = 'Stopping…';
  const r = await fetch('/api/runs/current/skip', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage: b.dataset.stage }) });
  if (!r.ok) { alert((await r.json()).detail || 'Failed'); b.disabled = false; b.textContent = '⏭ Skip stage'; }
});

$('stop-btn').addEventListener('click', async e => {
  const b = e.currentTarget;
  if (!confirm('Stop the whole run? What this run collected will not be saved, the reports stay from the previous run.')) return;
  b.disabled = true;
  b.textContent = 'Stopping…';
  const r = await fetch('/api/runs/current/stop', { method: 'POST' });
  if (!r.ok) { alert((await r.json()).detail || 'Failed'); b.disabled = false; b.textContent = '⏹ Stop run'; }
});

const stream = new EventSource('/api/progress/stream');
stream.addEventListener('progress', e => render(JSON.parse(e.data)));
// between events the "elapsed" time and the seconds of active sources keep ticking
setInterval(() => {
  if (!last || last.status !== 'running') return;
  last.elapsed += 1;
  (last.active || []).forEach(a => a.seconds += 1);
  if (last.eta_total != null) last.eta_total = Math.max(0, last.eta_total - 1);
  if (last.eta_stage != null) last.eta_stage = Math.max(0, last.eta_stage - 1);
  render(last);
}, 1000);

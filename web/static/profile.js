// Profile: tabs Header / Summary / Experience / Education. Items are compact rows, edited on click.
// State — objects as in the API (/api/profile, /api/cv-base/*); extra (keys the form does not show)
// is kept in the object and sent back as it was.
const cvData = JSON.parse(document.getElementById('cv-data').textContent);
const state = { summary: cvData.summary, jobs: cvData.experience.jobs };
let editing = null;  // the item object open for editing

// Editing an item: Done or a click outside it — collapse, edits stay; Cancel or Esc — restore it as it was
// before opening (a new item is removed on cancel). Saving to the file — only with the Save button.
let edit = null;  // { item, snapshot, rerender, list, isNew }
function openEdit(item, rerender, list = null, isNew = false) {
  if (edit && edit.item !== item && edit.rerender !== rerender) finishEdit();  // the open item is in another list
  edit = { item, snapshot: structuredClone(item), rerender, list, isNew };
  editing = item;
  rerender();
}
function finishEdit() {
  if (!edit) return;
  const { rerender } = edit;
  edit = null;
  editing = null;
  rerender();
}
function cancelEdit() {
  if (!edit) return;
  const { item, snapshot, list, isNew } = edit;
  if (isNew && list) list.splice(list.indexOf(item), 1);
  else { Object.keys(item).forEach(k => delete item[k]); Object.assign(item, snapshot); }
  finishEdit();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && edit) { e.preventDefault(); cancelEdit(); } });
document.addEventListener('mousedown', e => {
  // outside items (empty space, headers, Summary fields) — collapse; a click on another item opens it itself
  if (edit && !e.target.closest('.pf-item, .pf-tabs, button[type=submit]')) finishEdit();
});

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(c => c !== null && c !== undefined && c !== false));
  return node;
};
const csv = list => (list || []).join(', ');
const parseCsv = text => text.split(',').map(s => s.trim()).filter(Boolean);
const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

// --- tabs ---------------------------------------------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll('[data-pane]').forEach(p => { p.hidden = p.dataset.pane !== name; });
  document.querySelectorAll('.pf-tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  // one Save button right of the tabs — for the open tab (the button's form attribute)
  document.querySelectorAll('.pf-save [data-for]').forEach(n => { n.hidden = n.dataset.for !== name; });
  history.replaceState(null, '', '#' + name);
}
document.querySelectorAll('.pf-tabs button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
showTab(['head', 'summary', 'exp', 'edu'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'head');

// --- unsaved changes ----------------------------------------------------------------------------------------
const dirty = { summary: false, exp: false };
function markDirty(which) {
  dirty[which] = true;
  document.getElementById(which === 'summary' ? 'summary-status' : 'experience-status').textContent = '● not saved';
}
window.addEventListener('beforeunload', e => { if (dirty.summary || dirty.exp) e.preventDefault(); });

// --- fields -------------------------------------------------------------------------------------------------
function field(label, obj, key, kind, onChange, attrs = {}) {
  const input = kind === 'area' || kind === 'lines' ? el('textarea', { rows: attrs.rows || 2 }) : el('input', attrs);
  if (kind === 'number') input.type = 'number';
  input.value = kind === 'csv' ? csv(obj[key]) : kind === 'lines' ? (obj[key] || []).join('\n') : (obj[key] ?? '');
  input.addEventListener('input', () => {
    obj[key] = kind === 'csv' ? parseCsv(input.value)
      : kind === 'lines' ? input.value.split('\n').map(s => s.trim()).filter(Boolean)
      : kind === 'number' ? Number(input.value) : input.value;
    onChange();
  });
  return label ? el('label', { className: attrs.grow ? 'grow' : '' }, label, input) : input;
}

function variants(label, obj, key, onChange) {
  const box = el('span', { className: 'pf-variants' }, label);
  for (const v of ['backend', 'ai']) {
    const input = el('input', { type: 'checkbox', checked: (obj[key] || []).includes(v) });
    input.addEventListener('change', () => {
      obj[key] = ['backend', 'ai'].filter(x => x === v ? input.checked : (obj[key] || []).includes(x));
      onChange();
    });
    box.append(el('label', { className: 'inline' }, input, v));
  }
  return box;
}

const iconBtn = (text, title, fn, disabled = false) => {
  const b = el('button', { type: 'button', className: 'pf-icon', textContent: text, title, disabled });
  b.addEventListener('click', e => { e.stopPropagation(); fn(); });
  return b;
};

// --- drag and drop: a row is grabbed by the ⋮⋮ handle and dropped above or below another row of the same list ---
let drag = null;  // { list, index }
function sortable(node, handle, list, i, onMove) {
  handle.addEventListener('mousedown', () => { node.draggable = true; });
  handle.addEventListener('mouseup', () => { node.draggable = false; });
  node.addEventListener('dragstart', e => {
    e.stopPropagation();
    drag = { list, index: i };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', '');  // Firefox does not start dragging without data
    setTimeout(() => node.classList.add('pf-dragging'), 0);
  });
  node.addEventListener('dragend', () => {
    node.draggable = false;
    drag = null;
    document.querySelectorAll('.pf-drop-before, .pf-drop-after, .pf-dragging')
      .forEach(n => n.classList.remove('pf-drop-before', 'pf-drop-after', 'pf-dragging'));
  });
  const after = e => { const r = node.getBoundingClientRect(); return e.clientY > r.top + r.height / 2; };
  node.addEventListener('dragover', e => {
    if (!drag || drag.list !== list) return;  // no moving an item to another list (another workplace)
    e.preventDefault();
    e.stopPropagation();
    node.classList.toggle('pf-drop-after', after(e));
    node.classList.toggle('pf-drop-before', !after(e));
  });
  node.addEventListener('dragleave', () => node.classList.remove('pf-drop-before', 'pf-drop-after'));
  node.addEventListener('drop', e => {
    if (!drag || drag.list !== list) return;
    e.preventDefault();
    e.stopPropagation();
    const from = drag.index;
    let to = i + (after(e) ? 1 : 0);
    if (from < to) to -= 1;
    if (from !== to) {
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      onMove();
    }
  });
}
const handle = () => el('span', { className: 'pf-handle', textContent: '⋮⋮', title: 'drag up or down' });

// --- an item row: an achievement or an experience bullet ----------------------------------------------------
// opts: { variantKey, variantLabel, what, rerender, changed }
function itemRow(list, i, opts) {
  const item = list[i];
  const { rerender, changed } = opts;
  const change = () => { changed(); };
  const pin = iconBtn('📌', item.pin ? 'in every version — unpin' : 'pin: in every version of the CV',
    () => { item.pin = !item.pin; changed(); rerender(); });
  pin.classList.add('pf-pin');
  if (item.pin) pin.classList.add('on');
  const grip = handle();
  const actions = el('span', { className: 'pf-actions' },
    iconBtn('✕', `delete ${opts.what}`, () => {
      if (confirm(`Delete ${opts.what}?\n\n${item.text.slice(0, 140)}`)) { list.splice(i, 1); changed(); rerender(); }
    }, list.length === 1));

  const variantsOn = item[opts.variantKey] || [];
  const meta = el('div', { className: 'pf-meta' },
    ...(item.topics || []).map(t => el('span', { className: 'chip', textContent: t })),
    variantsOn.length ? el('span', { className: 'chip pf-chip-v', textContent: `${opts.variantShort}: ${variantsOn.join(', ')}` }) : null,
    (item.requires || []).length ? el('span', { className: 'chip pf-chip-r', textContent: `if: ${item.requires.join(', ')}` }) : null,
    Object.keys(item.extra || {}).length ? el('span', { className: 'chip', textContent: `+ ${Object.keys(item.extra).join(', ')}` }) : null);

  const body = el('div', { className: 'pf-body' });
  if (editing === item) {
    const text = field('', item, 'text', 'area', change, { rows: 3 });
    const topics = field('', item, 'topics', 'csv', change, { placeholder: 'api, sql, performance' });
    const done = el('button', { type: 'button', className: 'primary', textContent: 'Done', title: 'collapse, edits stay' });
    done.addEventListener('click', finishEdit);
    const cancel = el('button', { type: 'button', textContent: 'Cancel', title: 'restore as it was before opening (Esc)' });
    cancel.addEventListener('click', cancelEdit);
    const hints = el('div', { className: 'pf-hints' });
    const showHints = async () => {
      const found = await suggestTopics(item.text);
      const fresh = found.filter(t => !(item.topics || []).includes(t));
      hints.replaceChildren(...(fresh.length ? [el('span', { className: 'muted small', textContent: 'found in the text:' }),
        ...fresh.map(t => {
          const chip = el('button', { type: 'button', className: 'chip pf-hint', textContent: '+ ' + t, title: 'add the topic' });
          chip.addEventListener('click', () => {
            item.topics = [...(item.topics || []), t];
            topics.value = csv(item.topics);
            change();
            showHints();
          });
          return chip;
        })] : []));
    };
    let timer = 0;
    text.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(showHints, 300); });
    topics.addEventListener('input', showHints);
    showHints();
    body.append(text,
      el('div', { className: 'pf-row-fields' },
        el('label', { className: 'grow' }, 'Topics', topics),
        field('Only if topics', item, 'requires', 'csv', change, { placeholder: 'ai_tools, llm', grow: true }),
        variants(opts.variantLabel, item, opts.variantKey, change), cancel, done),
      hints);
    setTimeout(() => text.focus(), 0);
  } else {
    const p = el('div', { className: 'pf-text', innerHTML: item.text ? md(item.text) : '<i class="muted">empty item — click to write it</i>' });
    body.append(p, meta);
    body.addEventListener('click', () => openEdit(item, rerender));
  }
  const li = el('li', { className: 'pf-item' + (editing === item ? ' editing' : '') + (item.pin ? ' pinned' : '') },
    grip, pin, body, actions);
  sortable(li, grip, list, i, () => { changed(); rerender(); });
  return li;
}

// topics from the text — the same TOPICS dictionary the generator applies to a vacancy (the server counts: Python regexes)
const topicCache = new Map();
async function suggestTopics(text) {
  if (!text.trim()) return [];
  if (!topicCache.has(text)) {
    const r = await fetch('/api/cv-base/topics', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.slice(0, 5000) }) });
    topicCache.set(text, r.ok ? (await r.json()).topics : []);
  }
  return topicCache.get(text);
}

const newItem = variantKey => ({ text: '', pin: false, topics: [], requires: [], [variantKey]: [], extra: {} });

// --- Summary ------------------------------------------------------------------------------------------------
const summaryForm = document.getElementById('summary-form');
const summaryChanged = () => markDirty('summary');
summaryForm.elements.intro.replaceWith(field('', state.summary, 'intro', 'area', summaryChanged, { rows: 2 }));
summaryForm.elements.closing.replaceWith(field('', state.summary, 'closing', 'text', summaryChanged));
summaryForm.elements.max_achievements.replaceWith(field('', state.summary, 'max_achievements', 'number',
  () => { summaryChanged(); document.getElementById('max-label').textContent = state.summary.max_achievements; }, { min: 1, max: 10 }));

function renderAchievements() {
  document.getElementById('max-label').textContent = state.summary.max_achievements;
  const list = state.summary.achievements;
  document.getElementById('achievements').replaceChildren(...list.map((_, i) => itemRow(list, i, {
    variantKey: 'summary_for', variantLabel: 'in the base CV:', variantShort: 'base', what: 'achievement',
    rerender: renderAchievements, changed: summaryChanged })));
}
document.getElementById('add-achievement').addEventListener('click', () => {
  const item = newItem('summary_for');
  state.summary.achievements.push(item);
  summaryChanged();
  openEdit(item, renderAchievements, state.summary.achievements, true);
});

// --- experience ---------------------------------------------------------------------------------------------
const expChanged = () => markDirty('exp');
let editingJob = null;

function renderJobs() {
  document.getElementById('jobs').replaceChildren(...state.jobs.map((job, i) => {
    const rerender = renderJobs;
    const grip = handle();
    const title = el('div', { className: 'pf-job-info', title: 'click to edit the title, dates and stack' },
      el('div', { className: 'pf-job-title' }, el('b', { textContent: job.title || 'Job title' }), ' · ', job.company || 'Company',
        el('span', { className: 'pf-caret', textContent: editingJob === job ? ' ▴' : ' ▾' })),
      el('div', { className: 'muted small', textContent: [job.dates, job.tech.length ? job.tech.join(', ') : ''].filter(Boolean).join(' · ') }));
    title.addEventListener('click', () => { editingJob = editingJob === job ? null : job; rerender(); });
    const head = el('div', { className: 'pf-job-head' }, grip, title,
      el('span', { className: 'pf-actions' },
        iconBtn('✕', 'delete the workplace', () => {
          if (confirm(`Delete the workplace "${job.company}" with all its bullets?`)) { state.jobs.splice(i, 1); expChanged(); rerender(); }
        }, state.jobs.length === 1)));
    const details = editingJob === job ? el('div', { className: 'pf-job-edit' },
      el('div', { className: 'row3' }, field('Job title', job, 'title', 'text', expChanged), field('Company', job, 'company', 'text', expChanged),
        field('Dates', job, 'dates', 'text', expChanged, { placeholder: 'Jan 2022 – Present' })),
      field('Intro (one paragraph per line)', job, 'intro', 'lines', expChanged, { rows: 3 }),
      el('div', { className: 'pf-row-fields' }, field('Stack', job, 'tech', 'csv', expChanged, { grow: true }),
        field('Stack — always shown', job, 'tech_always', 'csv', expChanged, { grow: true }),
        field('Max bullets', job, 'bullets_max', 'number', expChanged, { min: 1, max: 30 }))) : null;
    const list = job.bullets;
    const bullets = el('ol', { className: 'pf-list' }, ...list.map((_, k) => itemRow(list, k, {
      variantKey: 'generic', variantLabel: 'only in the base CV:', variantShort: 'base only', what: 'bullet',
      rerender, changed: expChanged })));
    const add = el('button', { type: 'button', className: 'pf-add', textContent: '＋ Bullet' });
    add.addEventListener('click', () => { const b = newItem('generic'); list.push(b); expChanged(); openEdit(b, rerender, list, true); });
    const card = el('section', { className: 'card pf-job' }, head, details,
      el('div', { className: 'muted small pf-sub' }, `Bullets · up to ${job.bullets_max} in the CV`), bullets, add);
    sortable(card, grip, state.jobs, i, () => { expChanged(); rerender(); });
    return card;
  }));
}
document.getElementById('add-job').addEventListener('click', () => {
  const job = { title: '', company: '', dates: '', bullets_max: 4, intro: [], tech: [], tech_always: [],
    bullets: [newItem('generic')], extra: {} };
  state.jobs.push(job);
  editingJob = job;
  expChanged();
  renderJobs();
});

// --- saving -------------------------------------------------------------------------------------------------
async function put(url, body, statusId) {
  const status = document.getElementById(statusId);
  status.textContent = 'Saving…';
  const r = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) {
    status.textContent = '⚠️ ' + (typeof d.detail === 'string' ? d.detail
      : 'check: ' + d.detail.map(x => x.loc.slice(1).map(p => typeof p === 'number' ? p + 1 : p).join(' → ')).join('; '));
    return null;
  }
  status.textContent = '✔ saved';
  return d;
}

summaryForm.addEventListener('submit', async e => {
  e.preventDefault();
  const { file, ...body } = state.summary;
  const d = await put('/api/cv-base/summary', body, 'summary-status');
  if (d) { Object.assign(state.summary, d); dirty.summary = false; edit = editing = null; renderAchievements(); }
});
document.getElementById('experience-form').addEventListener('submit', async e => {
  e.preventDefault();
  const d = await put('/api/cv-base/experience', { jobs: state.jobs }, 'experience-status');
  if (d) { state.jobs = d.jobs; dirty.exp = false; edit = editing = editingJob = null; renderJobs(); }
});

// --- education, languages, publications ---------------------------------------------------------------------
const otherForm = document.getElementById('other-form');
otherForm.addEventListener('input', () => { document.getElementById('other-status').textContent = '● not saved'; });
otherForm.addEventListener('submit', async e => {
  e.preventDefault();
  const f = otherForm.elements;
  await put('/api/cv-base/other', {
    education: { degree: f.degree.value, school: f.school.value, years: f.years.value, thesis_ai: f.thesis_ai.value },
    languages: f.languages.value, publications: f.publications.value }, 'other-status');
});

// --- header -------------------------------------------------------------------------------------------------
const headForm = document.getElementById('profile-form');
const links = document.getElementById('links');
document.getElementById('add-link').addEventListener('click', () => {
  links.append(document.getElementById('link-tpl').content.cloneNode(true));
});
links.addEventListener('click', e => { if (e.target.classList.contains('del')) e.target.closest('.link-row').remove(); });
headForm.addEventListener('submit', async e => {
  e.preventDefault();
  const f = headForm.elements;  // form.name is the form's attribute, not the field
  const body = { name: f.name.value, email: f.email.value.trim(), phone: f.phone.value.trim(), location: f.location.value,
    links: [...links.querySelectorAll('.link-row')].map(r => ({ label: r.querySelector('[name=label]').value.trim(),
      url: r.querySelector('[name=url]').value.trim() })).filter(l => l.label || l.url) };
  await put('/api/profile', body, 'profile-status');
});

renderAchievements();
renderJobs();

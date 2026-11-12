// The CV page: form -> POST /api/cv -> the result with an explanation of the selection.
const form = document.getElementById('cv-form');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SOURCES = { text: 'text pasted by hand', page: 'read from the vacancy page' };

// ?url=…&one_page=1 — open the page with the form already filled in (a link from a report)
const params = new URLSearchParams(location.search);
if (params.get('url')) form.url.value = params.get('url');
if (params.get('one_page')) form.one_page.checked = true;

form.addEventListener('submit', async e => {
  e.preventDefault();
  const status = document.getElementById('cv-status');
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  status.textContent = 'Building: picking bullets, PDF' + (form.one_page.checked ? ', shrinking to one page' : '') + '…';
  const body = {
    url: form.url.value.trim(), text: form.text.value, company: form.company.value.trim(),
    title: form.title.value.trim(), one_page: form.one_page.checked, date: params.get('date') || '',
  };
  try {
    const r = await fetch('/api/cv', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'failed');
    show(d);
    status.textContent = '';
  } catch (err) {
    status.textContent = '⚠️ ' + err.message;
  } finally {
    button.disabled = false;
  }
});

function show(d) {
  document.getElementById('cv-result').hidden = false;
  document.getElementById('r-title').textContent = `${d.company} — ${d.title}`;
  const source = SOURCES[d.source] || `from the report of ${d.source}`;
  document.getElementById('r-meta').textContent =
    `Fit ${d.fit}% · ${d.pages} ${d.pages === 1 ? 'page' : 'pages'}${d.one_page && d.pages > 1 ? ' (did not fit even after shrinking)' : ''} · vacancy ${source}`;
  const vac = form.url.value.trim();
  if (vac) document.getElementById('r-meta').insertAdjacentHTML('beforeend', ` · <a href="${esc(vac)}" target="_blank" rel="noopener">vacancy ↗</a>`);
  document.getElementById('r-pdf').href = d.pdf;
  document.getElementById('r-md').href = d.md;
  document.getElementById('r-missing').innerHTML = d.missing.length
    ? `<div class="missing">In the vacancy, but not in the CV: ${d.missing.map(m => `<span class="chip">${esc(m)}</span>`).join(' ')}</div>`
    : '<div class="muted small">The CV has all key skills of the vacancy.</div>';
  document.getElementById('r-picks').innerHTML = d.picks.map(p => {
    const why = [
      p.pin ? 'pinned' : '',
      p.techs.length ? `technologies: ${p.techs.join(', ')}` : '',
      p.topics.length ? `topics: ${p.topics.join(', ')}` : '',
      p.similarity ? `text similarity ${Math.round(p.similarity * 100)}%` : '',
    ].filter(Boolean).join(' · ');
    const text = p.text.replace(/\*\*/g, '');
    return `<li><span class="pick-sec">${esc(p.section)}</span><span class="pick-score">${p.score}</span>
      <div><div>${esc(text.length > 170 ? text.slice(0, 170) + '…' : text)}</div><div class="muted small">${esc(why)}</div></div></li>`;
  }).join('');
}

if (params.get('url') && params.get('auto')) form.requestSubmit();

// The Cover letter page: form -> POST /api/cover -> the letter text, what is not covered, why the points were picked.
const form = document.getElementById('cover-form');
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SOURCES = { text: 'text pasted by hand', page: 'read from the vacancy page' };

// ?url=…&date=…&style=full&auto=1 — open with the form filled in (the button in a report)
const params = new URLSearchParams(location.search);
if (params.get('url')) form.url.value = params.get('url');
if (params.get('style') === 'full') form.style.value = 'full';

async function build() {
  const status = $('cover-status');
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  status.textContent = 'Building the letter…';
  const body = {
    url: form.url.value.trim(), text: form.text.value, company: form.company.value.trim(),
    title: form.title.value.trim(), style: form.style.value, date: params.get('date') || '',
  };
  try {
    const r = await fetch('/api/cover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'failed');
    show(d);
    status.textContent = '';
  } catch (err) {
    status.textContent = '⚠️ ' + err.message;
  } finally {
    button.disabled = false;
  }
}

form.addEventListener('submit', e => { e.preventDefault(); build(); });
// changing the style after the first build rebuilds at once
form.querySelectorAll('input[name=style]').forEach(r => r.addEventListener('change', () => {
  if (!$('cover-result').hidden) build();
}));

function show(d) {
  $('cover-result').hidden = false;
  $('r-title').textContent = `${d.company} — ${d.title}`;
  const source = SOURCES[d.source] || `from the report of ${d.source}`;
  $('r-meta').textContent = `Fit ${d.fit}% · ${d.style === 'full' ? 'full' : 'short'} · vacancy ${source}`;
  const vac = form.url.value.trim();
  if (vac) $('r-meta').insertAdjacentHTML('beforeend', ` · <a href="${esc(vac)}" target="_blank" rel="noopener">vacancy ↗</a>`);
  $('r-text').value = d.text;
  $('r-file').href = d.file;
  $('r-missing').innerHTML = d.missing.length
    ? `<div class="missing">In the vacancy, but not in your experience: ${d.missing.map(m => `<span class="chip">${esc(m)}</span>`).join(' ')}</div>`
    : '<div class="muted small">Your experience has all key skills of the vacancy.</div>';
  $('r-picks').innerHTML = d.picks.map(p => {
    const why = [
      p.techs.length ? `technologies: ${p.techs.join(', ')}` : '',
      p.topics.length ? `topics: ${p.topics.join(', ')}` : '',
      p.similarity ? `text similarity ${Math.round(p.similarity * 100)}%` : '',
    ].filter(Boolean).join(' · ') || 'added to reach the minimum';
    return `<li><span class="pick-sec">${esc(p.section)}</span><span class="pick-score">${p.score}</span>
      <div><div>${esc(p.text)}</div><div class="muted small">${esc(why)}</div></div></li>`;
  }).join('');
}

$('r-copy').addEventListener('click', async e => {
  await navigator.clipboard.writeText($('r-text').value);
  e.currentTarget.textContent = 'Copied ✓';
  setTimeout(() => { $('r-copy').textContent = 'Copy'; }, 1500);
});

if (params.get('url') && params.get('auto')) form.requestSubmit();

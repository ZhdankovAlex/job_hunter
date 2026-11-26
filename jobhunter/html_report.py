"""HTML version of the report: one self-contained page with a table, filters and sorting."""
from __future__ import annotations

import re
from datetime import date
from html import escape
from urllib.parse import quote

from . import config, cv_match, industry, outreach, report, salary, tiers

# The shared style base (colors, fonts, the size scale) — the same as the web UI's: the file is embedded into the page
# in full, so the report also opens without the server
TOKENS_CSS = (config.ROOT / "web" / "static" / "tokens.css").read_text(encoding="utf-8")

# Theme — the choice from the web UI toggle (localStorage "theme", see base.html). A page in a frame is served from the
# same origin and reads it before rendering, and the storage event switches it together with the UI; a page opened from
# disk (file:) follows the system. Goes into <head> of every page in reports/
THEME_JS = (
    "<script>(function(){if(location.protocol==='file:')return;var r=document.documentElement;"
    "function a(t){if(t==='light'||t==='dark')r.dataset.theme=t;else delete r.dataset.theme;}"
    "try{a(localStorage.getItem('theme'));}catch(e){}"
    "addEventListener('storage',function(e){if(e.key==='theme'||e.key===null)a(e.newValue);});})();</script>"
)

_CSS = TOKENS_CSS + """
* { box-sizing: border-box; }
body { margin: 0; padding: var(--sp-5) var(--sp-5) 60px; background: var(--bg); color: var(--fg);
       font: var(--fs-md)/var(--lh) var(--font-sans); }
h1 { font-size: var(--fs-xl); margin: 0 0 var(--sp-2); } h2 { font-size: var(--fs-lg); margin: 0; display: inline; }
.sub { color: var(--muted); margin-bottom: var(--sp-4); }
.warning { border: 1px solid var(--bad); background: var(--bad-soft); border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3);
           margin-bottom: var(--sp-4); font-weight: var(--fw-medium); }
/* filters and search — the same pills and field as in the UI (web/static/app.css: .toggles, input) */
.bar { position: sticky; top: 0; z-index: 2; background: var(--bg); padding: var(--sp-2) 0; display: flex; gap: var(--sp-1);
       flex-wrap: wrap; align-items: center; border-bottom: 1px solid var(--line); }
.bar button { display: inline-flex; align-items: center; height: var(--ctl-h-sm); padding: 0 10px; border: 1px solid var(--line);
              background: var(--chip); color: var(--fg); border-radius: var(--r-pill); cursor: pointer; font: inherit;
              font-size: var(--fs-sm); white-space: nowrap; }
.bar button:hover { border-color: var(--accent); }
.bar button.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.bar input { flex: 1; min-width: 220px; height: var(--ctl-h); margin-left: var(--sp-1); padding: 0 10px; border: 1px solid var(--line);
             border-radius: var(--r-md); background: var(--card); color: var(--fg); font: inherit; }
.bar input:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus); }
.count { color: var(--muted); white-space: nowrap; }
.wrap { overflow-x: auto; }
/* the main table has its own scroll, one screen high: the horizontal bar is always at the bottom of the window,
   the column header sticks; long cells wrap so the table is narrower */
.wrap.main-wrap { overflow: auto; max-height: calc(100vh - 64px); border-bottom: 1px solid var(--line); }
.main-wrap table { margin-top: 0; }
/* a floating horizontal bar at the bottom of the window — moves the table while the table is on screen */
.hscroll { position: fixed; bottom: 0; height: 16px; overflow-x: auto; overflow-y: hidden; z-index: 5;
           background: var(--bg); border-top: 1px solid var(--line); }
.hscroll div { height: 1px; }
/* the table's own horizontal bar is hidden — the floating .hscroll replaces it; the vertical one stays */
.main-wrap::-webkit-scrollbar { height: 0; width: 10px; } .main-wrap::-webkit-scrollbar-thumb { background: var(--line); border-radius: var(--r-sm); }
.main-wrap thead th { position: sticky; top: 0; z-index: 1; background: var(--bg); }
.main-wrap td.small { min-width: 140px; max-width: 280px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { cursor: pointer; user-select: none; white-space: nowrap; color: var(--muted); font-weight: var(--fw-medium); }
th:hover { color: var(--fg); }
tbody tr:hover { background: var(--row); }
td.n { font-variant-numeric: tabular-nums; font-weight: var(--fw-bold); text-align: right; }
th.n { text-align: right; }
td.small, .small { color: var(--muted); font-size: var(--fs-sm); }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.tag { display: inline-block; padding: 1px 7px; border-radius: var(--r-pill); background: var(--chip); font-size: var(--fs-xs);
       margin-right: 4px; white-space: nowrap; }
.tag.ind { color: var(--accent); } .tag.new { background: var(--accent); color: var(--on-accent); }
.tag.top { background: var(--warn-soft); color: var(--warn); }
.ok { color: var(--ok); } .warn { color: var(--warn); }
details { margin-top: 26px; border-top: 1px solid var(--line); padding-top: 14px; }
.fit { position: relative; min-width: 74px; height: 20px; border-radius: var(--r-sm); background: var(--chip); overflow: hidden; }
.fit i { position: absolute; inset: 0 auto 0 0; display: block; }
.fit b { position: relative; display: block; text-align: center; line-height: 20px; font-size: var(--fs-sm); }
summary { cursor: pointer; }
.reach { margin-top: 4px; font-size: var(--fs-sm); }
button.copy { border: 1px solid var(--line); background: var(--chip); color: var(--fg); cursor: pointer; font: inherit; }
button.copy:hover { border-color: var(--accent); }
/* a hover hint: data-tip="…" (like the tip component in web/static/app.css). The native title does not pop up
   in embedded browsers or waits a second — this one shows at once; with tabindex="0" — also on keyboard focus */
[data-tip] { position: relative; cursor: help; }
[data-tip]:hover::after, [data-tip]:focus-visible::after { content: attr(data-tip); position: absolute; left: 0;
  top: calc(100% + 6px); z-index: 10; width: max-content; max-width: 320px; padding: var(--sp-2) var(--sp-3);
  background: var(--fg); color: var(--bg); font-size: var(--fs-sm); font-weight: normal; line-height: 1.4;
  white-space: normal; border-radius: var(--r-md); box-shadow: var(--sh-2); pointer-events: none; }
"""

_JS = """
const rows = [...document.querySelectorAll('#main tbody tr')];
const search = document.getElementById('q'), count = document.getElementById('count');
let mode = 'all';
function apply() {
  const q = search.value.trim().toLowerCase(); let shown = 0;
  for (const r of rows) {
    const okMode = mode === 'all' || r.dataset[mode] === '1';
    const ok = okMode && (!q || r.textContent.toLowerCase().includes(q));
    r.hidden = !ok; if (ok) shown++;
  }
  count.textContent = shown + ' of ' + rows.length;
}
document.querySelectorAll('.bar button').forEach(b => b.addEventListener('click', () => {
  mode = b.dataset.mode;
  document.querySelectorAll('.bar button').forEach(x => x.classList.toggle('on', x === b));
  apply();
}));
search.addEventListener('input', apply);
document.querySelectorAll('table').forEach(table => {
  table.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => {
    const body = table.tBodies[0], dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    const key = tr => tr.children[i].dataset.sort ?? tr.children[i].textContent.trim().toLowerCase();
    [...body.rows].sort((a, b) => {
      const x = key(a), y = key(b), nx = parseFloat(x), ny = parseFloat(y);
      const cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y), 'en');
      return dir === 'asc' ? cmp : -cmp;
    }).forEach(tr => body.appendChild(tr));
  }));
});
document.querySelectorAll('button.copy').forEach(b => b.addEventListener('click', async () => {
  const text = b.dataset.msg;
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t);
    t.select(); document.execCommand('copy'); t.remove();
  }
  const label = b.textContent; b.textContent = '✔ copied'; setTimeout(() => b.textContent = label, 1500);
}));
// floating horizontal bar: the table's native bar is often below the edge of the screen
(function () {
  const wrap = document.querySelector('.main-wrap');
  if (!wrap) return;
  const bar = document.createElement('div'); bar.className = 'hscroll';
  const inner = document.createElement('div'); bar.appendChild(inner); document.body.appendChild(bar);
  let lock = false;
  bar.addEventListener('scroll', () => { if (lock) { lock = false; return; } lock = true; wrap.scrollLeft = bar.scrollLeft; });
  wrap.addEventListener('scroll', () => { if (lock) { lock = false; return; } lock = true; bar.scrollLeft = wrap.scrollLeft; });
  function place() {
    const r = wrap.getBoundingClientRect();
    const visible = r.top < innerHeight - 20 && r.bottom > 40 && wrap.scrollWidth > wrap.clientWidth;
    bar.style.display = visible ? 'block' : 'none';
    bar.style.left = r.left + 'px'; bar.style.width = wrap.clientWidth + 'px';
    inner.style.width = wrap.scrollWidth + 'px'; bar.scrollLeft = wrap.scrollLeft;
  }
  addEventListener('scroll', place, { passive: true }); addEventListener('resize', place);
  new ResizeObserver(place).observe(wrap); place();
})();
apply();
"""


def _e(text) -> str:
    return escape(str(text or ""))


def plural(n: int, one: str, many: str | None = None) -> str:
    """plural(3, "application") -> "3 applications"; plural(1, "day") -> "1 day"."""
    return f"{n} {one if n == 1 else many or one + 's'}"


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def short(d: date) -> str:
    """Nov 25 — independent of the locale."""
    return f"{MONTH_NAMES[d.month - 1]} {d.day}"


def _link(job) -> str:
    return job.url or getattr(job, "post_url", "")


def _date(job) -> str:
    return job.posted.date().isoformat() if job.posted else ""


def _linkify(text: str) -> str:
    """Makes links in the already escaped text of the marks clickable."""
    return re.sub(r"https://t\.me/[\w/]+", lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener">{m.group(0)}</a>', text)


WEB_URL = "http://127.0.0.1:8765"  # web UI (python -m web): builds a CV on a button click


def _cv_link(job, day: date) -> str:
    """The "📄 CV" and "1 page" buttons: the "CV" page of the web UI builds a CV for this vacancy.

    The link is absolute — it works from a report opened as a file too, if python -m web is running.
    If a CV was already built automatically (cvgen.auto_build), a link to it is next to them.
    """
    link = _link(job)
    if not link:
        return ""
    query = f"url={quote(link, safe='')}&date={day.isoformat()}&auto=1"
    out = (f' <a class="tag cv" href="{WEB_URL}/cv?{_e(query)}" target="_blank" '
           f'title="Build a CV for this vacancy (needs python -m web running)">📄 CV</a>'
           f'<a class="tag cv" href="{WEB_URL}/cv?{_e(query)}&amp;one_page=1" target="_blank" '
           f'title="One-page CV">1 page</a>')
    if getattr(job, "cv_file", ""):
        out += f'<a class="tag" href="../{_e(job.cv_file)}" target="_blank" title="built automatically">📄 ready</a>'
    out += (f'<a class="tag cv" href="{WEB_URL}/cover?{_e(query)}" target="_blank" '
            f'title="Cover letter for this vacancy (style — on the page)">✉️</a>')
    out += (f'<a class="tag cv" href="{WEB_URL}/prep?url={quote(link, safe="")}&amp;date={day.isoformat()}" '
            f'target="_blank" title="Interview prep: what to say, stories, questions">🎤</a>')
    applied = f"company={quote(job.company or '', safe='')}&title={quote(job.title or '', safe='')}&url={quote(link, safe='')}"
    out += (f'<a class="tag cv" href="{WEB_URL}/applications?{_e(applied)}" target="_blank" '
            f'title="Record the application in applications_manual.toml (form on the Applications page)">✅ applied</a>')
    return out


def _outreach_html(job) -> str:
    """A line under a top vacancy: LinkedIn people search and a "copy draft" button.

    The script does not visit LinkedIn — the user opens the links and sends the message.
    """
    links = [(label, url) for label, url in outreach.search_links(job.company)]
    if outreach.telegram_contact(job):
        links.insert(0, ("author in Telegram", outreach.telegram_contact(job)))
    parts = [f'<a href="{_e(url)}" target="_blank" rel="noopener">🔎 {_e(label)}</a>' for label, url in links]
    msg = outreach.draft(job)
    parts.append(f'<button class="tag copy" data-msg="{_e(msg)}" title="{_e(msg)}">✉️ to a recruiter</button>')
    ref = outreach.referral_draft(job)
    parts.append(f'<button class="tag copy" data-msg="{_e(ref)}" title="Referral request to a company engineer: '
                 f'{_e(ref)}">🤝 referral</button>')
    return f'<div class="reach">{" · ".join(parts)}</div>'


def _contacts_html(job) -> str:
    """Direct contacts from the vacancy text (email, Telegram, LinkedIn) — for any vacancy that has them."""
    text = getattr(job, "description", "") or getattr(job, "text", "")
    found = outreach.contacts(text, getattr(job, "channel", ""))
    if not found:
        return ""
    icons = {"email": "✉", "telegram": "✈", "linkedin": "in"}
    links = [f'<a href="{_e(href)}" target="_blank" rel="noopener">{icons[kind]} {_e(label)}</a>'
             for kind, label, href in found[:4]]
    return f'<div class="reach" title="contacts from the vacancy text">📇 {" · ".join(links)}</div>'


def _levels(job) -> str:
    """Second line in the salary column: the country median from levels.fyi, linked to the source."""
    note = getattr(job, "levels_note", "")
    if not note:
        return ""
    return (f'<div class="small"><a href="{_e(job.levels_url)}" target="_blank" rel="noopener" '
            f'title="Software Engineer, all grades, total compensation (base + bonuses + stock)">📊 {_e(note)}</a></div>')


def _industry_tags(job) -> str:
    """Industries of the vacancy as tags (industry.py)."""
    return "".join(f' <span class="tag ind" title="industry">{_e(industry.LABELS.get(ind, ind))}</span>'
                   for ind in (getattr(job, "industries", None) or []) if ind != "other")


def _missing(job) -> str:
    """What is missing: key skills, then nice-to-have."""
    core = ", ".join(getattr(job, "cv_core_missing", []))
    nice = ", ".join(getattr(job, "cv_nice_missing", []))
    return "; ".join(filter(None, [core, f"nice-to-have: {nice}" if nice else ""])) or "—"


def _fit_cell(job) -> str:
    """Fit % as a bar: green from 85, light green from 70, yellow from 50, red below."""
    fit = getattr(job, "cv_fit", -1)
    if fit < 0:
        return '<td data-sort="-1" class="small">—</td>'
    color = ("rgba(34,197,94,.55)" if fit >= 85 else "rgba(134,239,172,.6)" if fit >= 70
             else "rgba(250,204,21,.55)" if fit >= 50 else "rgba(248,113,113,.5)")
    tip = cv_match.skills_line(job)
    return (f'<td data-sort="{fit}" title="{_e(tip)}"><div class="fit"><i style="width:{fit}%;background:{color}"></i>'
            f"<b>{fit}%</b></div></td>")


def _table(headers: list[str], rows: list[str], table_id: str = "", num: tuple[int, ...] = ()) -> str:
    """num — indexes of numeric columns (td.n): their header is right-aligned, like the numbers."""
    head = "".join(f'<th class="n">{_e(h)}</th>' if i in num else f"<th>{_e(h)}</th>" for i, h in enumerate(headers))
    attr = f' id="{table_id}"' if table_id else ""
    wrap = "wrap main-wrap" if table_id == "main" else "wrap"
    return f'<div class="{wrap}"><table{attr}><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _applications_html(stats: dict) -> str:
    """The "📬 Applications" block above the table: counters and who to follow up with (full history — on the Applications page)."""
    digest = stats.get("applications")
    if not digest:
        return ""
    c = digest["counts"]
    parts = [f'<div class="warning">⚠️ {_e(digest["warning"])}</div>'] if digest.get("warning") else []
    parts += [
        '<div class="sub"><b>📬 Applications:</b> '
        f"🟢 in stages {c.get('active', 0) + c.get('offer', 0)} · 🟡 waiting for an answer {c.get('waiting', 0)} · "
        f"🟠 declined by you {c.get('declined', 0)} · 🔴 rejected {c.get('rejected', 0)} — "
        '<a href="applications.html">all applications</a></div>'
    ]
    return "".join(parts)


def _main_row(j, day: date, is_top: bool) -> str:
    """A row of the main table; data-* are for the filter buttons above the table."""
    status = report._status(j)
    is_new = j.is_new_today and not report._blocked(j)
    marks = "; ".join(j.marks) if hasattr(j, "channel") else report._marks(j)
    marks = "; ".join(m for m in marks.split("; ") if m and m != "🆕" and not m.startswith(("💰", "🧩")))
    fit = cv_match.summary(j)
    fit_class = "ok" if j.cv_full else ""
    low_pay = salary.below_min(j)
    pay_attr = ' class="warn" title="the range in the vacancy is below the [salary] min_monthly_eur threshold"' if low_pay else ""
    tags = ('<span class="tag new">new</span>' if is_new else "") + ('<span class="tag top">top</span>' if is_top else "")
    score = getattr(j, "score", 0)  # company priority from companies.txt
    roots = f' <span class="tag" title="company priority (companies.txt)">{"★" * score}</span>' if score else ""
    roots += _industry_tags(j)
    status_class = "ok" if status.startswith("✅") else "warn"
    contact = f' <span class="small">{_e(j.contact)}</span>' if getattr(j, "contact", "") and not j.url else ""
    return (
        f'<tr data-new="{int(is_new)}" data-top="{int(is_top)}" data-ok="{int(status.startswith("✅"))}" '
        f'data-check="{int(status.startswith("❓"))}" data-reloc="{int(status.startswith("🌍"))}" '
        f'data-fit="{int(bool(j.cv_full))}" data-prio="{int(score > 0)}" data-fit80="{int(j.cv_fit >= 80)}" '
        f'data-pay="{int(salary.meets_min(j))}">'
        f'<td data-sort="{j.tier}">{_e(tiers.NAMES[j.tier])}</td>'
        f'<td class="n">{j.relevance}</td>'
        f"{_fit_cell(j)}"
        f'<td data-sort="{"jmsl".find(j.grade[:1]) if j.grade else 9}">{_e(salary.GRADE_NAMES.get(j.grade, "—"))}</td>'
        f"<td>{_e(j.company)}{roots}</td>"
        f'<td>{tags}<a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a>{contact}'
        f'{_cv_link(j, day)}{_contacts_html(j)}{_outreach_html(j) if is_top else ""}</td>'
        f"<td>{_e(j.location or 'Remote')}</td>"
        f'<td class="{status_class}">{_e(status)}</td>'
        f"<td{pay_attr}>"
        f"{_e(j.salary_note)}{_levels(j)}</td>"
        f"<td>{_e(_date(j))}</td>"
        f'<td class="small">{_e(report._source(j))}</td>'
        f'<td class="small {fit_class}">{_e(fit)}</td>'
        f'<td class="small">{_e(_missing(j))}</td>'
        f'<td class="small">{_linkify(_e(marks))}</td></tr>'
    )


def _vacancy_link(j) -> str:
    return f'<a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a>'


def _refused_rows(refused: list) -> list[str]:
    return [
        f"<tr><td>{_e(j.company)}</td><td>{_vacancy_link(j)}</td>"
        f"<td>{_e(j.location)}</td><td>{_e(_date(j))}</td>"
        f'<td class="small">{_e("; ".join(filter(None, [j.applied_status, *j.history_marks])))}</td></tr>'
        for j in sorted(refused, key=lambda j: j.company.lower())
    ]


def _rejected_rows(rejected: list) -> list[str]:
    return [
        f'<tr><td class="n">{j.relevance}</td><td>{_e(j.company)}</td><td>{_vacancy_link(j)}</td>'
        f"<td>{_e(j.reject)}</td><td class=\"small\">{_e(j.reject_quote)}</td>"
        f'<td class="small">{_e(report._source(j))}</td></tr>'
        for j in report.pick_rejected(rejected)
    ]


def _staffing_rows(staffing: list) -> list[str]:
    return [
        f"<tr><td>{_e(j.company)}</td><td>{_vacancy_link(j)}</td>"
        f"<td>{_e(j.location)}</td><td class=\"small\">{_e(j.staffing)}</td></tr>"
        for j in sorted(staffing, key=lambda j: j.company.lower())
    ]


def _skill_gaps_html(main: list, tg: list) -> list[str]:
    gaps = report.skill_gaps(main, tg)
    if not gaps["total"]:
        return []
    nice, total = dict(gaps["nice"]), gaps["total"]
    gap_rows = []
    for name, n in gaps["core"]:
        extra = nice.pop(name, 0)
        extra_cell = f"{extra} ({round(100 * extra / total)}%)" if extra else "—"
        gap_rows.append(f'<tr><td>{_e(name)}</td><td class="n">{n} ({round(100 * n / total)}%)</td>'
                        f'<td class="n">{extra_cell}</td></tr>')
    return [f"<details><summary><h2>📊 Skills missing most often — over {gaps['total']} fitting "
            "vacancies (Backend, ML / AI)</h2></summary>",
            _table(["Skill", "Key (not in the CV)", "Nice-to-have"], gap_rows, num=(1, 2)), "</details>"]


def _filter_bar(rows: list[str], top: int) -> list[str]:
    """Filter buttons above the table: the number in brackets is how many rows have that mark (data-*)."""
    n = {key: sum(f'data-{key}="1"' in r for r in rows) for key in
         ("new", "ok", "check", "reloc", "fit", "prio", "fit80", "pay")}
    pay_min = salary.min_monthly()
    return [
        '<div class="bar">',
        f'<button data-mode="all" class="on">All ({len(rows)})</button>',
        f'<button data-mode="new">🆕 New ({n["new"]})</button>',
        f'<button data-mode="top">⭐ Top ({top})</button>',
        (f'<button data-mode="prio" title="companies with priority 1–3 in companies.txt">★ Priority ({n["prio"]})</button>'
         if n["prio"] else ""),
        f'<button data-mode="ok">✅ Confirmed ({n["ok"]})</button>',
        f'<button data-mode="fit80">Fit ≥ 80% ({n["fit80"]})</button>',
        f'<button data-mode="fit">✔ Full match ({n["fit"]})</button>',
        (f'<button data-mode="pay" title="Only vacancies whose text states a range with an upper bound of at least {pay_min} '
         f'EUR/month; the base estimate and levels.fyi do not count">💶 Range ≥ {pay_min / 1000:g}k ({n["pay"]})</button>' if pay_min else ""),
        f'<button data-mode="reloc">🌍 Relocation ({n["reloc"]})</button>',
        f'<button data-mode="check">❓ Check ({n["check"]})</button>',
        '<input id="q" type="search" placeholder="Search: company, role, skill, location…">',
        '<span class="count" id="count"></span></div>',
    ]


def _listed(j) -> bool:
    """In the main table: not "local language required" and not outsourcing (they have their own sections)."""
    return not j.lang_required and not j.staffing


def render(day: date, jobs: list, stats: dict, tg_jobs: list | None = None) -> str:
    """HTML report page. The sections are the same as in the Markdown report."""
    tg_jobs = tg_jobs or []
    staffing = [j for j in [*jobs, *tg_jobs] if j.staffing and not j.lang_required]
    refused, main = report._partition([j for j in jobs if _listed(j)], report._was_rejected)
    refused_tg, tg = report._partition([j for j in tg_jobs if _listed(j)], report._was_rejected)
    refused += refused_tg
    top_ids = {id(j) for j, _ in report.pick_review(main, tg)}
    rows = [_main_row(j, day, id(j) in top_ids) for j in sorted([*main, *tg], key=tiers.sort_key)]
    rejected = [j for j in stats.get("rejected", []) if not report._was_rejected(j)]
    refused_rows, rejected_rows, staffing_rows = _refused_rows(refused), _rejected_rows(rejected), _staffing_rows(staffing)

    def count(key: str) -> int:
        return sum(f'data-{key}="1"' in r for r in rows)

    parts = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Vacancies {day.isoformat()}</title>{THEME_JS}<style>{_CSS}</style></head><body>",
        f"<h1>Vacancies for {day.isoformat()}</h1>",
        f'<div class="sub">Total {len(rows)} · new {count("new")} · in the top {len(top_ids)} · '
        f"✅ {count('ok')} · 🌍 {count('reloc')} · ❓ {count('check')} · rejected before {len(refused)} · "
        f"rejected by filters {len(rejected)}</div>",
        _applications_html(stats),
        *_filter_bar(rows, len(top_ids)),
        _table(["Category", "Score", "Fit %", "Grade", "Company", "Vacancy", "Location", "Status", "Salary, EUR/month", "Date", "Source",
                "Match with the CV", "Missing", "Marks"],
               rows, "main", num=(1,)),
    ]
    if refused_rows:
        parts += [f"<details><summary><h2>Rejected before — {len(refused_rows)}</h2></summary>",
                  _table(["Company", "Vacancy", "Location", "Date", "Applications"], refused_rows), "</details>"]
    if rejected_rows:
        parts += [f"<details><summary><h2>Rejected by filters — {len(rejected)} (shown {len(rejected_rows)})</h2></summary>",
                  '<div class="small">Full list — state/rejected_latest.tsv</div>',
                  _table(["Raw score", "Company", "Vacancy", "Reason", "Quote", "Source"], rejected_rows, num=(0,)), "</details>"]
    parts += _skill_gaps_html(main, tg)
    if staffing_rows:
        parts += [f"<details><summary><h2>Staffing / outsourcing — {len(staffing_rows)}</h2></summary>",
                  _table(["Company", "Vacancy", "Location", "Reason"], staffing_rows), "</details>"]
    parts += [f"<script>{_JS}</script></body></html>"]
    return "\n".join(parts)

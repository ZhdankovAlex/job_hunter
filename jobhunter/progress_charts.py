"""Charts of the Progress page: events by day (applications, stages, correspondence, rejections) and the activity hex map.

The data (daily, hex_grid, hex_summary) is kept apart from the markup, so tests can check it. The browser draws the
daily chart (SVG + a little JS) from JSON embedded in the page — that is how the 30/90-day switch, the filters and
the tooltip work; the hex map is ready-made SVG. Colors are tokens.css variables only, no external libraries:
a page from reports/ opens without the server too.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from .html_report import _e, plural, short

if TYPE_CHECKING:
    from .gamification import Ledger

# chart series: Ledger book, label, color — the semantic UI colors from tokens.css, like the statuses on
# Applications. The order is bottom to top in a stack
SERIES = (
    ("applies", "Applications", "--accent"),
    ("stages", "Stages and calls", "--ok"),
    ("messages", "Correspondence", "--warn"),
    ("rejects", "Rejections", "--bad"),
)
PERIODS = (30, 90)
HEX_WEEKS = 12
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def daily(led: Ledger, today: date, days: int) -> list[dict[str, Any]]:
    """Events by day over `days` days up to today inclusive: {"d": "2026-11-25", "applies": 2, …}.

    One company on one day counts once in each series (as in Ledger).
    """
    start = today - timedelta(days=days - 1)
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append({"d": d.isoformat(), **{k: len(getattr(led, k).get(d, ())) for k, _, _ in SERIES}})
    return out


def period_totals(rows: list[dict[str, Any]], period: int) -> dict[str, tuple[int, int]]:
    """Series -> (sum over the last `period` days, sum over the `period` days before them)."""
    now, before = rows[-period:], rows[-2 * period:-period]
    return {k: (sum(r[k] for r in now), sum(r[k] for r in before)) for k, _, _ in SERIES}


@dataclass
class HexDay:
    day: date
    applies: int = 0
    stages: int = 0
    pings: int = 0

    @property
    def n(self) -> int:
        """Actions per day — as in the streak: applications, stages, own pings (a rejection or someone else's letter isn't our action)."""
        return self.applies + self.stages + self.pings


def hex_grid(led: Ledger, today: date, weeks: int = HEX_WEEKS) -> list[list[HexDay | None]]:
    """Weeks (columns, oldest on the left) × days Mon–Sun; None — the day hasn't come yet."""
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    grid: list[list[HexDay | None]] = []
    for w in range(weeks):
        col: list[HexDay | None] = []
        for i in range(7):
            d = start + timedelta(days=w * 7 + i)
            col.append(None if d > today else HexDay(d, len(led.applies.get(d, ())), len(led.stages.get(d, ())),
                                                     len(led.pings.get(d, ()))))
        grid.append(col)
    return grid


@dataclass
class HexSummary:
    active: int  # days with at least one action
    elapsed: int  # days in the map up to today inclusive
    busiest: HexDay | None
    per_active: float  # actions per active day on average
    total: int = field(default=0)


def hex_summary(grid: list[list[HexDay | None]]) -> HexSummary:
    days = [c for col in grid for c in col if c is not None]
    active = [c for c in days if c.n]
    total = sum(c.n for c in active)
    busiest = max(active, key=lambda c: (c.n, c.day), default=None)
    return HexSummary(len(active), len(days), busiest, total / len(active) if active else 0.0, total)


def cuts(grid: list[list[HexDay | None]]) -> tuple[int, int, int]:
    """Upper bounds of levels 1–3 — quartiles of the map's active days: everyone has their own pace, so at 20 actions
    a day the map doesn't "burn out" into one bright level, and at 1–2 it doesn't stay pale."""
    vals = sorted(c.n for col in grid for c in col if c is not None and c.n)
    if not vals:
        return 1, 2, 3
    q1, q2, q3 = (vals[min(len(vals) - 1, len(vals) * k // 4)] for k in (1, 2, 3))
    return q1, q2, q3


def level(n: int, bounds: tuple[int, int, int]) -> int:
    """Color level 0–4: 0 — no actions, then by quartiles (cuts)."""
    return 0 if n == 0 else 1 + sum(n > b for b in bounds)


# --- markup ---------------------------------------------------------------------------------

CSS = """
/* daily chart */
.game.trend .head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3);
                    flex-wrap: wrap; margin-bottom: var(--sp-3); }
.game.trend .head h2 { margin: 0; }
.seg { display: inline-flex; padding: 2px; gap: 2px; background: var(--chip); border-radius: var(--r-md); }
.seg button { height: var(--ctl-h-sm); padding: 0 10px; border: 0; border-radius: var(--r-sm); background: none;
              color: var(--muted); font: inherit; font-size: var(--fs-sm); cursor: pointer; }
.seg button.on { background: var(--card); color: var(--fg); box-shadow: var(--sh-2); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--sp-2);
        margin-bottom: var(--sp-3); }
.kpi { text-align: left; font: inherit; color: var(--fg); cursor: pointer; padding: var(--sp-2) var(--sp-3);
       border: 1px solid var(--line); border-radius: var(--r-md); background: none;
       background-image: linear-gradient(180deg, color-mix(in srgb, var(--c, var(--accent)) 12%, transparent), transparent 70%); }
.kpi:hover { border-color: var(--c, var(--accent)); }
.kpi[aria-pressed="false"] { background-image: none; opacity: .45; }
.kpi .name { font-size: var(--fs-sm); color: var(--muted); display: flex; align-items: center; gap: 6px; }
.kpi .dot { width: 8px; height: 8px; border-radius: var(--r-pill); background: var(--c, var(--accent)); }
.kpi .v { font-size: var(--fs-xl); font-weight: var(--fw-bold); font-variant-numeric: tabular-nums; }
.kpi .delta { font-size: var(--fs-xs); color: var(--muted); margin-left: var(--sp-1); }
.chart { position: relative; min-height: 240px; }
.chart svg { display: block; overflow: visible; }
.chart .ax { font-size: var(--fs-xs); fill: var(--muted); font-family: var(--font-mono); }
.chart .grid { stroke: var(--line); stroke-dasharray: 2 4; }
.chart .col-hl { fill: var(--chip); }
.chart .empty { font-size: var(--fs-sm); fill: var(--muted); }
.chart-tip { position: absolute; top: 0; pointer-events: none; background: var(--card); border: 1px solid var(--line);
             border-radius: var(--r-md); box-shadow: var(--sh-2); padding: var(--sp-2) var(--sp-3); font-size: var(--fs-sm);
             white-space: nowrap; z-index: 2; }
.chart-tip .row { display: flex; align-items: center; gap: 6px; }
.chart-tip .row b { margin-left: auto; padding-left: var(--sp-3); font-variant-numeric: tabular-nums; }
.chart-tip .dot { width: 8px; height: 8px; border-radius: var(--r-pill); background: var(--c, var(--accent)); }
/* activity hex map */
.game.hexes .body { display: grid; grid-template-columns: minmax(260px, 1.1fr) minmax(220px, 1fr); gap: var(--sp-5);
                    align-items: start; }
.hexmap { position: relative; }
.hexmap svg { max-width: 100%; height: auto; display: block; }  /* not stretched: labels stay at the scale's size */
.hexmap .ax { font-size: var(--fs-xs); fill: var(--muted); font-family: var(--font-mono); }
/* levels are shades of one --accent and read by saturation, so hover leaves the fill alone and draws only an
   outline; today is a dashed outline */
.hex { fill: var(--chip); }
.hex.h1 { fill: color-mix(in srgb, var(--accent) 25%, var(--chip)); }
.hex.h2 { fill: color-mix(in srgb, var(--accent) 50%, var(--chip)); }
.hex.h3 { fill: color-mix(in srgb, var(--accent) 75%, var(--chip)); }
.hex.h4 { fill: var(--accent); }
.hex.today { stroke: var(--fg); stroke-width: 1.5; stroke-dasharray: 3 2; }
.hexmap use[data-t]:hover { stroke: var(--fg); stroke-width: 2; stroke-dasharray: none; }
.hex-legend { display: flex; align-items: center; justify-content: flex-end; gap: 3px; max-width: 440px;
              font-size: var(--fs-xs); color: var(--muted); margin-top: var(--sp-1); }
.hex-legend svg { width: 12px; height: 13px; }
.hex-stats .big { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); padding-bottom: var(--sp-3);
                  border-bottom: 1px solid var(--line); }
.hex-stats .lbl { font-size: var(--fs-sm); color: var(--muted); }
.hex-stats .num { font-size: var(--fs-xl); font-weight: var(--fw-bold); font-variant-numeric: tabular-nums; }
.hex-stats .hi { color: var(--ok); }
.hex-stats dl { margin: 0; }
.hex-stats dl > div { display: flex; justify-content: space-between; gap: var(--sp-3); padding: var(--sp-2) 0;
                      border-bottom: 1px solid var(--line); }
.hex-stats dl > div:last-child { border-bottom: 0; }
.hex-stats dt { color: var(--muted); } .hex-stats dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 720px) { .game.hexes .body { grid-template-columns: 1fr; } }
"""

# The daily chart: stacked columns — a day's count is a whole number and there is nothing between days, so no
# smoothing (a curve would draw "half a day" and plateaus where one kind of event gave way to another). The fill is
# a gradient of the series color. Tiles on top are the period sums and the series filter; the tooltip shows the
# numbers of the day under the cursor.
SCRIPT = """<script>
(() => {
const root = document.getElementById('trend'); if (!root) return;
const data = JSON.parse(document.getElementById('trend-data').textContent);
const box = root.querySelector('.chart'), tip = root.querySelector('.chart-tip');
const NS = 'http://www.w3.org/2000/svg', WD = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const el = (n, a, p) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]);
  if (p) p.appendChild(e); return e; };
const md = iso => MON[+iso.slice(5, 7) - 1] + ' ' + (+iso.slice(8, 10));
let period = data.periods[0]; const on = new Set(data.series.map(s => s.k));
function kpis() {
  const rows = data.days.slice(-period), prev = data.days.slice(-2 * period, -period);
  root.querySelectorAll('.kpi').forEach(b => {
    const k = b.dataset.k, v = rows.reduce((a, r) => a + r[k], 0), p = prev.reduce((a, r) => a + r[k], 0);
    b.querySelector('.v').textContent = v;
    const d = b.querySelector('.delta');
    d.textContent = prev.length < period ? '' : v > p ? '↑' + (v - p) : v < p ? '↓' + (p - v) : '=';
    d.title = prev.length < period ? '' : 'previous ' + period + ' days: ' + p;
    b.setAttribute('aria-pressed', on.has(k));
  });
}
function draw() {
  const W = Math.max(320, box.clientWidth), H = 240, L = 30, R = 4, T = 12, B = 26;
  const rows = data.days.slice(-period), keys = data.series.filter(s => on.has(s.k));
  const tot = rows.map(r => keys.reduce((a, s) => a + r[s.k], 0));
  const peak = Math.max(0, ...tot);
  const step = [1, 2, 5, 10, 20, 50, 100].find(x => peak / x <= 4) || 100, top = Math.max(step, Math.ceil(peak / step) * step);
  const slot = (W - L - R) / rows.length, bw = Math.max(2, Math.min(28, slot * .72));
  const cx = i => L + slot * (i + .5), y = v => T + (H - T - B) * (1 - v / top);
  const svg = box.querySelector('svg'); svg.replaceChildren();
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
  const defs = el('defs', {}, svg);
  for (const s of data.series) {  // a gradient over the whole chart height: brighter on top, dimmer below — the same for every column
    const g = el('linearGradient', {id: 'tg-' + s.k, gradientUnits: 'userSpaceOnUse', x1: 0, y1: T, x2: 0, y2: H - B}, defs);
    el('stop', {offset: '0%', style: `stop-color:var(${s.c})`}, g);
    el('stop', {offset: '100%', style: `stop-color:var(${s.c});stop-opacity:.55`}, g);
  }
  const hl = el('rect', {class: 'col-hl', y: T, height: H - T - B, width: slot, rx: 4, visibility: 'hidden'}, svg);
  for (let v = 0; v <= top; v += step) {
    el('line', {class: 'grid', x1: L, x2: W - R, y1: y(v), y2: y(v)}, svg);
    el('text', {class: 'ax', x: L - 6, y: y(v) + 4, 'text-anchor': 'end'}, svg).textContent = v;
  }
  const every = Math.ceil(rows.length / 6);
  for (let i = rows.length - 1; i >= 0; i -= every)
    el('text', {class: 'ax', x: cx(i), y: H - 6, 'text-anchor': i === rows.length - 1 ? 'end' : 'middle'}, svg)
      .textContent = md(rows[i].d);
  if (!peak) {
    el('text', {class: 'empty', x: (L + W - R) / 2, y: (T + H - B) / 2, 'text-anchor': 'middle'}, svg)
      .textContent = 'No events in ' + period + ' days';
  }
  const gap = bw > 6 ? 1 : 0;  // a thin gap between the layers of a column
  rows.forEach((r, i) => {
    let base = 0;
    for (const s of keys) {
      const v = r[s.k]; if (!v) continue;
      const y0 = y(base), y1 = y(base + v);
      el('rect', {x: cx(i) - bw / 2, y: y1, width: bw, height: Math.max(1, y0 - y1 - gap), rx: Math.min(3, bw / 4),
                  fill: `url(#tg-${s.k})`}, svg);
      base += v;
    }
  });
  const hit = el('rect', {x: L, y: 0, width: W - L - R, height: H, fill: 'transparent'}, svg);
  hit.addEventListener('mousemove', ev => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(rows.length - 1, Math.floor((ev.clientX - r.left - L) / slot))), row = rows[i];
    hl.setAttribute('x', L + slot * i); hl.setAttribute('visibility', 'visible');
    const wd = WD[new Date(row.d + 'T12:00').getDay()];
    tip.innerHTML = `<div><b>${wd} ${md(row.d)}</b> · total ${tot[i]}</div>` + keys.slice().reverse().map(s =>
      `<div class="row"><span class="dot" style="--c:var(${s.c})"></span>${s.name}<b>${row[s.k]}</b></div>`).join('');
    tip.hidden = false;
    const xi = cx(i), left = xi + 14 + tip.offsetWidth > W ? xi - 14 - tip.offsetWidth : xi + 14;
    tip.style.left = left + 'px'; tip.style.top = T + 'px';
  });
  hit.addEventListener('mouseleave', () => { hl.setAttribute('visibility', 'hidden'); tip.hidden = true; });
}
root.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => {
  period = +b.dataset.p; root.querySelectorAll('.seg button').forEach(x => x.classList.toggle('on', x === b));
  kpis(); draw();
}));
root.querySelectorAll('.kpi').forEach(b => b.addEventListener('click', () => {
  const k = b.dataset.k; if (on.has(k) && on.size > 1) on.delete(k); else on.add(k);
  kpis(); draw();
}));
kpis(); draw();
let wait; addEventListener('resize', () => { clearTimeout(wait); wait = setTimeout(draw, 100); });
})();
(() => {  // the hex map tooltip: the same card as the chart's
  const map = document.querySelector('.hexmap'); if (!map) return;
  const tip = map.querySelector('.chart-tip');
  map.addEventListener('mouseover', ev => {
    const h = ev.target.closest('[data-t]'); if (!h) { tip.hidden = true; return; }
    tip.textContent = h.dataset.t; tip.hidden = false;
    const m = map.getBoundingClientRect(), r = h.getBoundingClientRect();
    tip.style.left = Math.min(r.left - m.left, m.width - tip.offsetWidth) + 'px';
    tip.style.top = (r.top - m.top - tip.offsetHeight - 6) + 'px';
  });
  map.addEventListener('mouseleave', () => { tip.hidden = true; });
})();
</script>"""


def _json(data: Any) -> str:
    """JSON inside <script>: "</" must not close the tag."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def trend_html(rows: list[dict[str, Any]]) -> str:
    """The Trend block: filter tiles with sums, the period switch, room for the chart. rows — 2 × max(PERIODS)."""
    first = PERIODS[0]
    totals = period_totals(rows, first)
    seg = "".join(f'<button type="button" data-p="{p}" class="{"on" if p == first else ""}">{p} days</button>'
                  for p in PERIODS)
    tiles = "".join(
        f'<button type="button" class="kpi" data-k="{k}" aria-pressed="true" style="--c:var({c})">'
        f'<span class="name"><span class="dot"></span>{_e(name)}</span>'
        f'<span class="v">{totals[k][0]}</span><span class="delta"></span></button>'
        for k, name, c in SERIES)
    data = {"periods": list(PERIODS), "days": rows,
            "series": [{"k": k, "name": name, "c": c} for k, name, c in SERIES]}
    return (
        '<section class="game trend" id="trend"><div class="head"><h2>Trend</h2>'
        f'<div class="seg" role="group" aria-label="Period">{seg}</div></div>'
        f'<div class="kpis">{tiles}</div>'
        '<div class="chart"><svg role="img" aria-label="Events by day"></svg><div class="chart-tip" hidden></div></div>'
        '<div class="small">One company on one day counts once in each series. The tiles turn series on and off; '
        'the arrow is the difference from the previous period of the same length.</div>'
        f'<script type="application/json" id="trend-data">{_json(data)}</script></section>'
    )


_R = 17.0  # hex radius (center to vertex); pointy top
_HW = math.sqrt(3) * _R  # horizontal step
_HEX = " ".join(f"{(_R - 1.6) * math.cos(math.radians(a)):.2f},{(_R - 1.6) * math.sin(math.radians(a)):.2f}"
                for a in range(-90, 270, 60))
_ROW_LABELS = {0: "Mon", 2: "Wed", 4: "Fri"}


def _hex_tip(c: HexDay) -> str:
    when = f"{WEEKDAYS[c.day.weekday()]} {short(c.day)}"
    if not c.n:
        return f"{when}: no actions"
    parts = [plural(n, word) for n, word in ((c.applies, "application"), (c.stages, "stage"), (c.pings, "ping")) if n]
    return f"{when}: {', '.join(parts)}"


def hexmap_svg(grid: list[list[HexDay | None]], today: date) -> str:
    """Hexes: columns are weeks, rows are days Mon–Sun, odd rows shifted by half a step."""
    left, top = 40.0, 30.0
    bounds = cuts(grid)
    width = left + (len(grid) + 0.5) * _HW + 2
    height = top + 6 * 1.5 * _R + _R + 2
    out = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}" role="img" aria-label="Activity by day">'
           f'<defs><polygon id="hx" points="{_HEX}"/></defs>']
    for row, label in _ROW_LABELS.items():
        out.append(f'<text class="ax" x="0" y="{top + row * 1.5 * _R + 4:.1f}">{label}</text>')
    for w, col in enumerate(grid):
        if w % 3 == 0 and col[0] is not None:
            out.append(f'<text class="ax" x="{left + w * _HW - _HW / 2:.1f}" y="12">{short(col[0].day)}</text>')
        for row, c in enumerate(col):
            if c is None:
                continue
            x = left + w * _HW + (_HW / 2 if row % 2 else 0)
            y = top + row * 1.5 * _R
            cls = f"hex h{level(c.n, bounds)}" + (" today" if c.day == today else "")
            tip = _e(_hex_tip(c))
            out.append(f'<use href="#hx" x="{x:.1f}" y="{y:.1f}" class="{cls}" data-t="{tip}"><title>{tip}</title></use>')
    out.append("</svg>")
    return "".join(out)


def _legend(bounds: tuple[int, int, int]) -> str:
    one = f'<svg viewBox="-15 -15 30 30"><polygon points="{_HEX}" class="hex h{{}}"/></svg>'
    b1, b2, b3 = bounds
    ranges = [str(b1) if b1 <= 1 else f"1–{b1}"] + [
        str(hi) if hi == lo + 1 else f"{lo + 1}–{hi}" for lo, hi in ((b1, b2), (b2, b3)) if hi > lo] + [f"{b3 + 1}+"]
    tip = "Actions per day by level: " + ", ".join(ranges) + " — by quartiles of the map's active days"
    return (f'<div class="hex-legend" data-tip="{_e(tip)}" tabindex="0">fewer '
            + "".join(one.format(i) for i in range(5)) + " more</div>")


def hexes_html(grid: list[list[HexDay | None]], today: date, extra: list[tuple[str, str]]) -> str:
    """The Activity block: the hex map over HEX_WEEKS weeks and the summary. extra — summary rows (label, value HTML)."""
    s = hex_summary(grid)
    pct = round(100 * s.active / s.elapsed) if s.elapsed else 0
    busiest = (f"{WEEKDAYS[s.busiest.day.weekday()]} {short(s.busiest.day)} · "
               f"{_e(plural(s.busiest.n, 'action'))}") if s.busiest else "—"
    rows = [("Busiest day", busiest),
            ("Average per active day", f"{s.per_active:.1f}" if s.active else "—"),
            *extra]
    dl = "".join(f"<div><dt>{_e(k)}</dt><dd>{v}</dd></div>" for k, v in rows)
    return (
        f'<section class="game hexes"><h2>Activity over {HEX_WEEKS} weeks</h2><div class="body">'
        f'<div class="hexmap">{hexmap_svg(grid, today)}{_legend(cuts(grid))}<div class="chart-tip" hidden></div></div>'
        '<div class="hex-stats"><div class="big">'
        f'<div><div class="lbl">✦ Active days</div><div class="num">{s.active}</div>'
        f'<div class="small"><span class="hi">{pct}%</span> of {s.elapsed} days</div></div>'
        f'<div><div class="lbl">⚡ Actions</div><div class="num">{s.total}</div>'
        f'<div class="small">over {HEX_WEEKS} weeks</div></div>'
        f'</div><dl>{dl}</dl></div></div>'
        '<div class="small">Actions are applications, stages and your own pings to recruiters; one company on one day is one action.</div>'
        "</section>"
    )

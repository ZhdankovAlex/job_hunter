"""Progress charts: events by day (applications, stages, correspondence, rejections) and the activity hex map."""
import json
import re
from datetime import UTC, date, datetime, timedelta

from jobhunter import gamification as g, progress_charts as pc
from jobhunter.mail import APPLIED, INTERVIEW, OTHER, REJECTED, Application, Event

MON = date(2026, 11, 2)  # a Monday


def ev(d: date, kind: str, thread: str = "t1") -> Event:
    return Event(datetime(d.year, d.month, d.day, 12, tzinfo=UTC), kind, "", "hr@example.com", thread)


def day(n: int) -> date:
    return MON + timedelta(days=n)


def test_daily_counts_each_kind_once_per_company_and_day():
    led = g.ledger([
        Application("Acme", [ev(day(0), APPLIED), ev(day(0), APPLIED), ev(day(2), OTHER), ev(day(4), INTERVIEW)]),
        Application("Globex", [ev(day(0), APPLIED), ev(day(2), OTHER, thread=""), ev(day(3), REJECTED)]),
    ])
    rows = pc.daily(led, day(4), 5)
    assert [r["d"] for r in rows] == [day(i).isoformat() for i in range(5)]
    assert rows[0] == {"d": day(0).isoformat(), "applies": 2, "stages": 0, "messages": 0, "rejects": 0}
    assert rows[2]["messages"] == 2  # a letter in a thread and an own ping — both are correspondence
    assert rows[3]["rejects"] == 1 and rows[4]["stages"] == 1


def test_period_totals_compare_with_previous_period():
    led = g.ledger([Application("Acme", [ev(day(0), APPLIED), ev(day(5), APPLIED), ev(day(6), APPLIED)])])
    rows = pc.daily(led, day(6), 6)  # day(1)..day(6)
    assert pc.period_totals(rows, 3)["applies"] == (2, 0)
    assert pc.period_totals(rows, 3)["rejects"] == (0, 0)


def test_hex_grid_counts_own_actions_only():
    led = g.ledger([
        Application("Acme", [ev(day(1), APPLIED), ev(day(1), OTHER, thread=""), ev(day(1), OTHER)]),
        Application("Globex", [ev(day(1), INTERVIEW), ev(day(2), REJECTED)]),
    ])
    grid = pc.hex_grid(led, day(3), weeks=2)
    assert len(grid) == 2 and grid[0][0] is not None and grid[0][0].day == day(-7)
    cell = grid[1][1]
    assert cell is not None and (cell.applies, cell.stages, cell.pings, cell.n) == (1, 1, 1, 3)  # a letter in a thread — no
    assert grid[1][2] is not None and grid[1][2].n == 0  # a rejection isn't our action
    assert grid[1][4] is None  # Friday hasn't come yet


def test_hex_summary_active_days_and_busiest():
    led = g.ledger([Application("Acme", [ev(day(0), APPLIED), ev(day(2), APPLIED)]),
                    Application("Globex", [ev(day(2), APPLIED)])])
    s = pc.hex_summary(pc.hex_grid(led, day(3), weeks=1))
    assert (s.active, s.elapsed, s.total) == (2, 4, 3)
    assert s.busiest is not None and (s.busiest.day, s.busiest.n) == (day(2), 2)
    assert s.per_active == 1.5
    empty = pc.hex_summary(pc.hex_grid(g.ledger([]), day(3), weeks=1))
    assert (empty.active, empty.busiest, empty.per_active) == (0, None, 0.0)


def test_levels_by_quartiles_of_active_days():
    def grid(*ns: int) -> list[list[pc.HexDay | None]]:
        return [[pc.HexDay(day(i), applies=n) for i, n in enumerate(ns)]]
    bounds = pc.cuts(grid(1, 1, 2, 3, 5))
    assert [pc.level(n, bounds) for n in (0, 1, 2, 3, 5)] == [0, 1, 2, 3, 4]
    busy = pc.cuts(grid(8, 12, 15, 20, 21))  # many actions a day — the map doesn't "burn out" into one level
    assert sorted({pc.level(n, busy) for n in (8, 12, 15, 20, 21)}) == [1, 2, 3, 4]
    assert pc.cuts(grid(0, 0)) == (1, 2, 3)


def test_page_embeds_chart_data_and_hexes(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "CV_DIR", tmp_path)
    monkeypatch.setattr(g, "JOBS_DIR", tmp_path)
    page = g.page(day(3), [(Application("Acme</script>", [ev(day(1), APPLIED)]), "applied")])
    raw = re.search(r'<script type="application/json" id="trend-data">(.*?)</script>', page, re.S)
    assert raw is not None
    data = json.loads(raw.group(1))
    assert data["periods"] == list(pc.PERIODS) and len(data["days"]) == 2 * max(pc.PERIODS)
    assert data["days"][-3]["applies"] == 1
    assert page.count('<use href="#hx"') == 7 * (pc.HEX_WEEKS - 1) + 4  # the current week — up to Thursday
    assert "Tue Nov 3: 1 application" in page

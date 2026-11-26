"""Daily vacancy collection -> reports/jobs_YYYY-MM-DD.md. Sends nothing anywhere."""
import argparse
import io
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta

from jobhunter import (
    aggregators,
    applied,
    config,
    cvgen,
    filters,
    freehire,
    geo,
    html_report,
    industry,
    keys,
    levels,
    progress,
    report,
    salary as salary_rules,
    sites,
    telegram,
    tiers,
    verify,
)
from jobhunter.applications_report import StatusIndex
from jobhunter.ats import PLATFORMS, AtsClient
from jobhunter.companies import load_companies
from jobhunter.cv_match import CvProfile
from jobhunter.fsutil import atomic_write_text
from jobhunter.http import Http, cancel as http_cancel
from jobhunter.models import norm_name
from jobhunter.relevance import Profile
from jobhunter.salary import SalaryBase
from jobhunter.staffing import Blacklist

STAGES = [
    ("companies", "Company list"),
    ("freehire", "freehire.me: search"),
    ("sources", "ATS boards, aggregators, career pages"),
    ("filter", "Filters and freshness"),
    ("freehire_texts", "freehire.me: full texts and checks"),
    ("sheets", "Applications: applications_manual.toml and statuses from mail"),
    ("telegram", "Telegram channels"),
    ("telegram_check", "Checking Telegram vacancies"),
    ("scoring", "Matching against the CV, scores, salaries"),
    ("cv", "CVs for vacancies"),
    ("reports", "Reports"),
]
SKIPPABLE = {"freehire", "sources", "telegram", "cv"}  # stages with a "Skip" button in the web UI
SOURCES_FILE = config.ROOT / "state" / "sources_latest.json"  # how many vacancies each source gave
FREEHIRE_FILE = config.ROOT / "state" / "freehire_latest.json"  # freehire queries: name, parameters, result

log = logging.getLogger("jobhunter")


def setup_logging():
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = progress.ConsoleHandler(sys.stdout)  # prints the log above the progress line
    console.setFormatter(fmt)
    progress.rotate_errors_log()
    errors = logging.FileHandler(config.ERRORS_LOG, encoding="utf-8")
    errors.setLevel(logging.WARNING)
    errors.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, errors])
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", help="only these companies (comma-separated)")
    p.add_argument("--limit", type=int, help="only the first N companies of the list")
    p.add_argument("--rediscover", action="store_true", help="search the ATS slug again for all companies")
    p.add_argument("--no-aggregators", action="store_true", help="skip Arbeitnow/RemoteOK/Himalayas")
    p.add_argument("--no-freehire", action="store_true", help="skip freehire.me")
    p.add_argument("--no-telegram", action="store_true", help="skip Telegram channels")
    p.add_argument("--no-applied", action="store_true", help="do not check applications (applications_manual.toml)")
    return p.parse_args()


def collect(args, companies, http, ats, career_pages=()):
    """All sources in parallel; the 1 req/s limit is kept for each host separately."""
    jobs = []
    track = progress.tracked  # marks the source as "in work" for the time of the request
    kinds: dict[str, str] = {}  # source -> kind: ats / aggregator / site / career_page
    results: dict[str, dict] = {}  # result per source -> state/sources_latest.json
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        futures = {}
        for c in companies:
            futures[pool.submit(track, c.name, ats.jobs_for, c, args.rediscover)] = c.name
            kinds[c.name] = "ats"
        if not args.no_aggregators:
            for name in aggregators.SOURCES:
                futures[pool.submit(track, name, aggregators.fetch, name, http)] = name
                kinds[name] = "aggregator"
            for name in sites.enabled_sources():
                futures[pool.submit(track, name, sites.fetch, name, http)] = name
                kinds[name] = "site"
        for company, url, *location in career_pages:  # career pages without an ATS
            futures[pool.submit(track, company.name, sites.career_page, http, company, url, *location)] = company.name
            kinds[company.name] = "career_page"
        progress.set_total(len(futures))
        try:
            for done, future in enumerate(as_completed(futures), 1):
                if progress.skip_requested():  # the "Skip" button: unfinished sources are cancelled
                    log.warning("Sources: skipped with the button, processed %d of %d", done - 1, len(futures))
                    http_cancel.set()  # running requests are cut, the pool does not wait for slow career pages
                    break
                name = futures[future]
                try:
                    found = future.result()
                    jobs.extend(found)
                    results[name] = {"kind": kinds[name], "jobs": len(found), "error": ats.failures.get(name, "")}
                    progress.tally("source_kinds", kinds[name], len(found))
                    if found:
                        progress.event("source", f"{name}: {len(found)} vacancies")
                except Exception as e:
                    log.exception("%s: unexpected error — skipped", name)
                    results[name] = {"kind": kinds[name], "jobs": 0, "error": f"{type(e).__name__}: {e}"[:200]}
                if done % 50 == 0:
                    log.info("Sources processed: %d/%d", done, len(futures))
        finally:
            pool.shutdown(cancel_futures=True)
            http_cancel.clear()
            ats.save()
            for name, entry in results.items():
                entry.update((ats.mapping.get(name) or {}) if entry["kind"] == "ats" else {})
            atomic_write_text(SOURCES_FILE, json.dumps(results, ensure_ascii=False, indent=1))
    return jobs


_REASON_GROUPS = [  # reject reason -> short group for the chart on the pipeline page
    (("location", "on-site", "tied to country", "Russia only"), "location"),
    (("language", "JVM"), "other language"),
    (("full-time", "AI trainer"), "not full-time"),
    (("experience from",), "experience"),
    (("older than",), "old"),
]


def _reason_group(reason: str) -> str:
    """"primary language — Go" -> "other language"; an unknown reason is kept as is, up to the colon."""
    for needles, group in _REASON_GROUPS:
        if any(n in reason for n in needles):
            return group
    return reason.split(":")[0][:40]


def save_freehire_queries(fh, settings: dict, jobs: list) -> None:
    """freehire queries of the last run -> state/freehire_latest.json (the "Sources" page).

    For each query: the name from settings.toml, parameters, how many vacancies the site has in total
    and how many were received (a vacancy found by several queries counts in each).
    """
    got = Counter(q for j in jobs for q in j.extra.get("queries", []))
    rows = [{
        "name": q["name"], "title": q.get("title", q["name"]),
        "params": fh.query_params.get(q["name"], {}),
        "total": fh.totals.get(q["name"]), "fetched": got.get(q["name"], 0),
        "skipped": q["name"] not in fh.query_params,  # for example, relocation is turned off
    } for q in settings.get("queries", [])]
    atomic_write_text(FREEHIRE_FILE, json.dumps(rows, ensure_ascii=False, indent=1))


def load_seen() -> dict[str, str]:
    """Vacancy key -> date (ISO) when the script first saw it."""
    try:
        return json.loads(config.SEEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        # a broken file (a write cut off before atomic writes, a hand edit) must not fail the run:
        # every vacancy just becomes new
        log.warning("%s is broken — starting over", config.SEEN_FILE)
        return {}


def is_fresh(job, seen: dict[str, str], today: date, max_age_days: int) -> bool:
    """Published no earlier than max_age_days ago; without a date — counted from the first sighting."""
    cutoff = today - timedelta(days=max_age_days)
    if job.posted:
        return job.posted.astimezone(UTC).date() >= cutoff
    first_seen = seen.get(keys.norm_url(job.url))
    return first_seen is None or date.fromisoformat(first_seen) >= cutoff


def mark_first_seen(jobs, tg_jobs, today, seen):
    """Seen vacancies are kept between runs; new = first seen today."""
    keyed = [(job, keys.norm_url(job.url)) for job in jobs]
    keyed += [
        (job, f"tg:{keys.company_key(job.company)}|{norm_name(job.title)}") for job in tg_jobs
    ]
    for job, key in keyed:
        job.is_new_today = seen.setdefault(key, today.isoformat()) == today.isoformat()
    config.SEEN_FILE.parent.mkdir(exist_ok=True)
    atomic_write_text(config.SEEN_FILE, json.dumps(seen, indent=0, sort_keys=True))


def _section_rank(job):
    if job.reloc_type:
        return 1 + geo.TYPE_ORDER.index(job.reloc_type)
    return 99 if job.location_check else 0


def must_report(ats, fetched, matched):
    """Summary lines for must-report companies: found / passed filters / reason."""
    found = Counter(j.company for j in fetched if j.source in PLATFORMS)
    passed = Counter(j.company for j in matched if j.source in PLATFORMS)
    rows = []
    for name in geo.S.get("must_report_companies", []):
        entry = ats.mapping.get(name)
        override = geo.S.get("ats_overrides", {}).get(name, {}).get("greenhouse_board_token")
        if name in ats.failures:
            reason = ats.failures[name]
        elif override or (entry and entry.get("platform")):
            reason = ""
        elif entry is None:
            reason = "check not finished: network failure or 429 while guessing the slug"
        else:
            reason = "the slug fits none of Greenhouse, Lever, Ashby, Workable"
            if entry.get("empty_board"):
                reason += f"; there is only an empty board {entry['empty_board']}"
        if reason:  # a cached "not found" would otherwise not reach errors.log every day
            log.warning("%s: ATS board unavailable — %s", name, reason)
        rows.append((name, found[name], passed[name], reason))
    return rows


def enrich_from_ats(tg_jobs, ats):
    """For posts with a link to Greenhouse/Lever/Ashby/… fetches the full vacancy text from the board."""
    from jobhunter.models import Company

    boards: dict[tuple[str, str], list] = {}
    for job in tg_jobs:
        if job.full_text or not job.url:
            continue
        board = keys.ats_from_link(job.url)
        if board:
            boards.setdefault(board, []).append(job)
    filled = 0
    for (platform, slug), posts in boards.items():
        try:
            board_jobs = ats._fetch(platform, slug, Company(posts[0].company)) or []
        except Exception as e:  # network, format — the post keeps the ❓ status
            log.warning("telegram: could not read the board %s/%s (%s)", platform, slug, e)
            continue
        by_url = {keys.norm_url(j.url): j for j in board_jobs}
        by_id = {i: j for j in board_jobs for i in keys.url_ids(j.url)}
        for post in posts:
            twin = by_url.get(keys.norm_url(post.url)) or next(
                (by_id[i] for i in keys.url_ids(post.url) if i in by_id), None)
            if twin and twin.description:
                post.text = twin.description
                post.full_text = True
                if twin.location and not post.location:
                    post.location = twin.location
                filled += 1
    return filled


# How many NEW companies from Telegram posts without full text to look up on ATS boards per run: finding a board
# takes up to ten requests per company (Workable — one per 6 s); the result, "not found" included, is cached in
# ats_mapping.json, so known companies do not count against the limit and later runs are fast
TG_BOARD_LOOKUPS = 25


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", title.lower()).strip()


_TG_BOARD_CACHE: dict[str, list] = {}  # company -> vacancies of its board (within one run)


def _enrich_from_company_boards(tg_jobs, ats, lookups: dict | None = None) -> int:
    """A post without full text (a digest with a link to an aggregator such as wantapply.com, which blocks
    bots) -> find the company board (Greenhouse/Lever/Ashby/Workable) and take the vacancy text with the same title.
    This shows requirements missing from the post: office, language, experience."""
    from jobhunter.models import Company

    filled, looked = 0, _TG_BOARD_CACHE
    lookups = lookups if lookups is not None else {"new": 0}
    for job in tg_jobs:
        if job.full_text or not job.company or not filters.title_candidate(job.title):
            continue
        name = job.company.strip()
        if name not in looked:
            if name not in ats.mapping:
                if lookups["new"] >= TG_BOARD_LOOKUPS:
                    continue
                lookups["new"] += 1
            try:
                # quiet: "board not found" for a company from a post is normal, nothing to act on
                looked[name] = ats.jobs_for(Company(name), quiet=True) or []
            except Exception as e:  # network, format — the post keeps the text from Telegram
                log.warning("telegram: board %s not read (%s)", name, e)
                looked[name] = []
        key = _title_key(job.title)
        twin = next((b for b in looked[name] if b.description and _title_key(b.title) == key), None)
        if twin:
            job.text = twin.description
            job.full_text = True
            if twin.location and not job.location:
                job.location = twin.location
            filled += 1
    return filled


def merge_duplicates(jobs):
    """One vacancy (company + title) in several locations -> one row."""
    merged: dict = {}
    for job in jobs:
        key = (keys.company_key(job.company), norm_name(job.title))
        base = merged.get(key)
        if base is None:
            merged[key] = job
            continue
        locations = list(dict.fromkeys(
            part.strip() for j in (base, job) for part in j.location.split(";") if part.strip()
        ))
        # the section is taken from the best location: ✅ > 🌍 (confirmed earlier) > ❓
        best = min(base, job, key=_section_rank)
        sources = list(dict.fromkeys([*(base.sources or [base.source]), *(job.sources or [job.source])]))
        extra = base.extra or job.extra
        geo_fields = (best.location_check, best.reloc_country, best.reloc_type, best.reloc_quote)
        if ((job.posted and job.posted.timestamp()) or 0) > ((base.posted and base.posted.timestamp()) or 0):
            merged[key] = base = job
        base.location = "; ".join(locations)
        base.sources, base.extra = sources, extra
        base.location_check, base.reloc_country, base.reloc_type, base.reloc_quote = geo_fields
    return list(merged.values())


def main():
    args = parse_args()
    setup_logging()
    pipeline = progress.Pipeline("main", STAGES, skippable=SKIPPABLE)
    try:
        run(args)
    except BaseException:  # Ctrl+C and sys.exit included: the stage and the run are marked as failed
        log.exception("Run interrupted")
        pipeline.finish(ok=False)
        raise
    pipeline.finish()


@dataclass
class RunContext:
    """State of one run: the stages of run() read and extend it in turn.

    Fields with field(init=False) appear at their stage — reading one earlier gives AttributeError, not None.
    """

    args: argparse.Namespace
    today: date
    http: Http = field(init=False)
    ats: AtsClient = field(init=False)
    fh: freehire.Freehire = field(init=False)
    fh_settings: dict = field(default_factory=dict)
    companies: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)  # norm_name -> Company: score and note for aggregator vacancies
    career_pages: list[tuple] = field(default_factory=list)
    fetched: list = field(default_factory=list)
    fh_jobs: list = field(default_factory=list)
    seen: dict[str, str] = field(default_factory=dict)
    max_age_days: int = 30
    matched: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    stale: int = 0
    fh_candidates: list = field(default_factory=list)
    fh_added: int = 0
    fh_twins: int = 0
    fh_described: int = 0
    fh_dropped: int = 0
    apps: applied.Applications | None = None
    app_index: StatusIndex | None = None
    blacklist: Blacklist = field(init=False)
    fresh: list = field(default_factory=list)
    unmerged: int = 0
    tg_jobs: list | None = None  # None — Telegram is off (no section in the report), [] — on, but nothing found
    tg_failed: list = field(default_factory=list)
    tg_enriched: int = 0
    profile: Profile = field(init=False)
    salaries: SalaryBase = field(init=False)

    @property
    def reported(self) -> list:
        """Vacancies of the report: new ones from ATS/sites and Telegram posts that passed the checks."""
        return [*self.fresh, *(self.tg_jobs or [])]


def run(args: argparse.Namespace) -> None:
    """The whole run: collection, filters, Telegram, matching against the CV, reports — stages in progress order."""
    ctx = RunContext(args, date.today())
    _stage_companies(ctx)
    ctx.http = Http()
    _stage_freehire(ctx)
    _career_pages(ctx)
    progress.start("sources")
    ctx.fetched = collect(args, ctx.companies, ctx.http, ctx.ats, ctx.career_pages)
    progress.count(fetched=len(ctx.fetched))
    _stage_filter(ctx)
    _stage_freehire_texts(ctx)
    _stage_sheets(ctx)
    _stage_telegram(ctx)
    mark_first_seen(ctx.fresh, ctx.tg_jobs or [], ctx.today, ctx.seen)
    _stage_scoring(ctx)
    _stage_salaries(ctx)
    _score_rejected(ctx)
    _stage_reports(ctx, _stats(ctx))


def _stage_companies(ctx: RunContext) -> None:
    """The company list; --only / --limit narrow the crawl, but not the score table."""
    progress.start("companies")
    ctx.companies = load_companies()
    ctx.scores = {norm_name(c.name): c for c in ctx.companies}
    if ctx.args.only:
        wanted = {norm_name(n) for n in ctx.args.only.split(",")}
        ctx.companies = [c for c in ctx.companies if norm_name(c.name) in wanted]
    if ctx.args.limit:
        ctx.companies = ctx.companies[: ctx.args.limit]


def _stage_freehire(ctx: RunContext) -> None:
    ctx.fh_settings = geo.S.get("freehire", {})
    ctx.fh = freehire.Freehire(ctx.http, ctx.fh_settings)
    if ctx.fh_settings.get("enabled", True) and not ctx.args.no_freehire:
        progress.start("freehire")
        try:  # the main source goes first; a parameter dropped by the API stops the run
            ctx.fh_jobs = ctx.fh.collect()
        except freehire.IgnoredParamsError as e:
            log.error("%s", e)
            sys.exit(2)
        save_freehire_queries(ctx.fh, ctx.fh_settings, ctx.fh_jobs)
    else:
        progress.skip("freehire", "turned off")


def _career_pages(ctx: RunContext) -> None:
    ctx.ats = AtsClient(ctx.http)
    log.info("Companies to check: %d", len(ctx.companies))
    by_name = {c.name: c for c in ctx.companies}
    # pages from settings.toml [career_pages]: company = link or { url, location, links }. They were picked by hand,
    # so they are read even for outsourcing companies — such vacancies go to the "Staffing/outsourcing" appendix
    for name, page in geo.S.get("career_pages", {}).items():
        if name in by_name:
            page = page if isinstance(page, dict) else {"url": page}
            ctx.career_pages.append((by_name[name], page["url"], page.get("location", ""), page.get("links", "")))


def _stage_filter(ctx: RunContext) -> None:
    """Filters for every collected vacancy: duplicates, role and rules (filters.apply), freshness."""
    progress.start("filter", total=len(ctx.fetched))
    ctx.seen = load_seen()
    ctx.max_age_days = int(geo.S.get("max_age_days", 30))
    seen_urls: set[str] = set()
    for job in ctx.fetched:
        known = ctx.scores.get(norm_name(job.company))
        if known and not job.score:  # a vacancy from an aggregator, the company is in our list
            job.score, job.note = known.score, known.note
        key = keys.norm_url(job.url) if job.url else ""
        progress.step(f"{job.company} — {job.title}")
        if key in seen_urls:
            progress.tally("rejects", "duplicate")
            continue
        if not filters.apply(job):
            if job.reject:  # the role fits, but another rule fired — show it in "Rejected"
                seen_urls.add(key)
                ctx.rejected.append(job)
                progress.tally("rejects", _reason_group(job.reject))
                progress.event("reject", f"{job.company} — {job.title}: {job.reject}")
            else:
                progress.tally("rejects", "wrong role")
            continue
        seen_urls.add(key)
        priority = keys.company_key(job.company) in geo.PRIORITY_COMPANIES
        if not priority and not is_fresh(job, ctx.seen, ctx.today, ctx.max_age_days):
            ctx.stale += 1
            job.reject = f"older than {ctx.max_age_days} days"
            ctx.rejected.append(job)
            progress.tally("rejects", "old")
            continue
        ctx.matched.append(job)
        progress.tally("rejects", "passed")
        progress.event("found", f"{job.company} — {job.title} ({job.location or 'remote'})")


def _stage_freehire_texts(ctx: RunContext) -> None:
    """freehire: the full text for candidates and a check by it; at the end blocked_urls are dropped."""
    progress.count(matched=len(ctx.matched))
    progress.start("freehire_texts")
    candidates = ctx.fh_candidates = _freehire_candidates(ctx)[: int(ctx.fh_settings.get("descriptions_top", 200))]
    missing_text = [j for j in candidates if not j.extra.get("full_description")]
    if missing_text:  # the search already gives the full text; an extra request is only for rare exceptions
        ctx.fh.fill_descriptions(missing_text)
    ctx.fh_described = sum(bool(j.extra.get("full_description")) for j in candidates)
    for job in candidates:
        if freehire.prepare(job):
            ctx.fh_added += 1
            ctx.matched.append(job)
            progress.tally("rejects", "passed")
            progress.event("found", f"{job.company} — {job.title} (freehire)")
        else:
            ctx.rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject or "wrong role"))
    ctx.fh_dropped = len(candidates) - ctx.fh_added
    ctx.fh.diagnose_empty()

    blocked_urls = {keys.norm_url(u) for u in geo.S.get("blocked_urls", [])}
    ctx.matched = [j for j in ctx.matched if keys.norm_url(j.url) not in blocked_urls]


def _freehire_candidates(ctx: RunContext) -> list:
    """freehire vacancies without a twin among the found ones that pass the prefilter; twins only get the source.

    A twin is matched by URL or by company + title.
    """
    by_url = {keys.norm_url(j.url): j for j in ctx.matched}
    by_title = {(keys.company_key(j.company), norm_name(j.title)): j for j in ctx.matched}
    max_experience = int(ctx.fh_settings.get("max_experience_years", 7))
    candidates = []
    for job in ctx.fh_jobs:
        url_key, title_key = keys.norm_url(job.url), (keys.company_key(job.company), norm_name(job.title))
        twin = by_url.get(url_key) or by_title.get(title_key)
        if twin is not None:
            if not twin.extra:
                ctx.fh_twins += 1
                twin.extra = job.extra
                twin.sources = ["freehire", *(twin.sources or [twin.source])]
            continue
        known = ctx.scores.get(norm_name(job.company))
        if known:
            job.score, job.note = known.score, known.note
        if freehire.prefilter(job, max_experience):
            candidates.append(job)
        elif job.reject:
            ctx.rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject))
    return candidates


def _stage_sheets(ctx: RunContext) -> None:
    """Check against applications (mail, applications_manual.toml); merge duplicates; staffing and referral marks."""
    progress.start("sheets")
    apps = ctx.apps = None if ctx.args.no_applied else applied.load_applications()
    try:  # application statuses from mail (local cache) and applications_manual.toml
        ctx.app_index = StatusIndex(apps)
    except Exception:
        log.exception("Could not read application statuses — there will be no \"📬\" marks")
        ctx.app_index = None
    fresh = []
    for job in ctx.matched:
        if apps and apps.has_job(job):
            continue
        if apps:
            job.applied_status = apps.company_statuses(job.company)
            status = apps.job_status(job)
            if status is not None:
                job.history_marks.append(f"🔁 already applied to this vacancy: {status or 'no status'}")
        fresh.append(job)
    ctx.unmerged = len(fresh)
    ctx.fresh = merge_duplicates(fresh)
    ctx.blacklist = Blacklist()
    for job in ctx.fresh:
        job.staffing = ctx.blacklist.reason(job.company, job.title, job.description)
        job.app_status = ctx.app_index.lookup(job.company) if ctx.app_index else ""
        job.referral = keys.company_key(job.company) in geo.REFERRAL_COMPANIES


def _stage_telegram(ctx: RunContext) -> None:
    """Telegram: collect the channels and run the same text checks."""
    use_telegram = config.TELEGRAM_ENABLED and config.TELEGRAM_CHANNELS and not ctx.args.no_telegram
    if not use_telegram:
        why = "--no-telegram" if ctx.args.no_telegram else "turned off in settings.toml [telegram]"
        progress.skip("telegram", why)
        progress.skip("telegram_check", why)
        ctx.tg_enriched = enrich_from_ats([], ctx.ats)
        return
    progress.start("telegram", total=len(config.TELEGRAM_CHANNELS))
    tg_all, ctx.tg_failed = telegram.collect(ctx.http)
    if tg_all is None:
        ctx.tg_enriched = enrich_from_ats([], ctx.ats)
        return
    progress.start("telegram_check", total=len(tg_all))
    tg_jobs: list = []
    ctx.tg_jobs = tg_jobs
    ctx.tg_enriched = enrich_from_ats(tg_all, ctx.ats)
    lookups = {"new": 0}  # how many new companies were already looked up on ATS in this run (limit TG_BOARD_LOOKUPS)
    for job in tg_all:
        progress.step(f"@{job.channel}: {job.title}")
        verify.check(job, job.text, job.full_text)
        # the post passed on a short text (a digest with a link to wantapply etc.) — take the full text from
        # the company board and check again: requirements like office or language are only there
        if not job.reject and not job.full_text and _enrich_from_company_boards([job], ctx.ats, lookups):
            ctx.tg_enriched += 1
            verify.check(job, job.text, job.full_text)
        if job.reject:
            ctx.rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject))
            progress.event("reject", f"@{job.channel}: {job.company} — {job.title}: {job.reject}")
            continue
        progress.tally("rejects", "passed")
        progress.event("found", f"@{job.channel}: {job.company} — {job.title}")
        if ctx.apps:
            job.applied_status = ctx.apps.company_statuses(job.company)
        job.staffing = ctx.blacklist.reason(job.company, job.title, job.text)
        job.app_status = ctx.app_index.lookup(job.company) if ctx.app_index else ""
        if job.app_status:
            job.marks.append(f"📬 {job.app_status}")
        tg_jobs.append(job)


def _stage_scoring(ctx: RunContext) -> None:
    """CV Fit, tier, industry and the relevance score."""
    tg_jobs = ctx.tg_jobs or []
    progress.count(new=len(ctx.fresh), telegram=len(tg_jobs))
    progress.start("scoring")
    cv = CvProfile(geo.S.get("cv", {}))
    for job in ctx.fresh:
        cv.analyze(job, job.description)
    for job in tg_jobs:
        cv.analyze(job, job.text)
    for job in ctx.reported:
        job.tier = tiers.tier(job)
        job.industries = industry.classify(job.company, job.title, getattr(job, "description", "") or getattr(job, "text", ""))

    profile = ctx.profile = Profile()
    for job in ctx.fresh:
        job.relevance, job.matched_skills = profile.score(
            job, job.description, job.lang_marks, profile.location_points(job)
        )
    for job in tg_jobs:
        job.relevance, job.matched_skills = profile.score(
            job, job.text, job.marks, profile.tg_location_points(job)
        )


def _stage_salaries(ctx: RunContext) -> None:
    """Adds to the base of salary ranges and labels every vacancy: stated range or estimate; median by country."""
    today = ctx.today
    salaries = ctx.salaries = SalaryBase()
    for job in telegram.SALARY_POSTS:
        region = "cis" if job.channel in config.TELEGRAM_CIS_CHANNELS else "global"
        salaries.add(f"tg:{job.post_url}:{norm_name(job.title)}", job.title, job.salary, region,
                     f"@{job.channel}", (job.posted or today).isoformat()[:10])
    for job in [*ctx.fetched, *ctx.fh_jobs]:
        if job.salary:
            salaries.add(keys.norm_url(job.url), job.title, job.salary, "global", job.source,
                         (job.posted.date() if job.posted else today).isoformat())
    salaries.save()
    for job in ctx.reported:
        job.grade = salary_rules.grade(job.title, getattr(job, "description", "") or getattr(job, "text", ""))
        job.salary_note = salaries.describe(job.title, job.salary, job.grade)
    # median for the vacancy country from levels.fyi (cached for a week — a few requests per run)
    market = levels.Levels(ctx.http)
    for job in ctx.reported:
        job.levels_note, job.levels_url = market.describe(job.location, getattr(job, "reloc_country", ""))
    market.save()


def _score_rejected(ctx: RunContext) -> None:
    """The "raw score" of rejected ones — only skills and title, without location adjustments; no repeats."""
    unique_rejected: dict = {}
    for job in ctx.rejected:
        text = getattr(job, "description", "") or getattr(job, "text", "")
        job.relevance, job.matched_skills = ctx.profile.base_score(job, text)
        key = (keys.company_key(job.company), norm_name(job.title))
        if key not in unique_rejected or job.relevance > unique_rejected[key].relevance:
            unique_rejected[key] = job
    ctx.rejected = sorted(unique_rejected.values(), key=lambda j: -j.relevance)
    for job in ctx.rejected:
        if ctx.apps:
            job.applied_status = ctx.apps.company_statuses(job.company)
    rejected_file = config.ROOT / "state" / "rejected_latest.tsv"
    rejected_file.write_text(
        "score\tcompany\ttitle\treason\tquote\turl\n" + "\n".join(
            "\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in (
                j.relevance, j.company, j.title, j.reject, j.reject_quote, j.url or getattr(j, "post_url", "")))
            for j in ctx.rejected),
        encoding="utf-8",
    )


def _stats(ctx: RunContext) -> dict:
    """Run summary for the reports (report.render, html_report.render)."""
    mapping, companies, fh = ctx.ats.mapping, ctx.companies, ctx.fh
    return {
        "fetched": len(ctx.fetched),
        "matched": len(ctx.matched),
        "in_applied": len(ctx.matched) - ctx.unmerged,
        "companies": len(companies),
        "mapped": sum(1 for c in companies if (mapping.get(c.name) or {}).get("platform")),
        "tg_failed": ctx.tg_failed,
        "merged": ctx.unmerged - len(ctx.fresh),
        "stale": ctx.stale,
        "salary": {"samples": len(ctx.salaries.samples), "summary": ctx.salaries.summary()},
        "tg_enriched": ctx.tg_enriched,
        "freehire": {
            "enabled": bool(ctx.fh_jobs) or fh.requests > 0 or fh.cache_hits > 0,
            "fetched": len(ctx.fh_jobs), "added": ctx.fh_added, "twins": ctx.fh_twins,
            "requests": fh.requests, "cache_hits": fh.cache_hits, "totals": fh.totals,
            "described": ctx.fh_described, "dropped": ctx.fh_dropped,
            "diagnosis": fh.diagnosis, "candidates": len(ctx.fh_candidates),
        },
        "rejected": ctx.rejected,
        "applications": ctx.app_index.digest() if ctx.app_index else None,
        "max_age_days": ctx.max_age_days,
        "referral": [(n, bool((mapping.get(n) or {}).get("platform")))
                     for n in geo.S.get("referral_companies", [])],
        "must_report": must_report(ctx.ats, ctx.fetched, ctx.matched),
    }


def _stage_reports(ctx: RunContext, stats: dict) -> None:
    """CV auto-build (the cv stage), MD/HTML reports and the vacancies JSON (the reports stage)."""
    today, fresh, tg_jobs = ctx.today, ctx.fresh, ctx.tg_jobs
    config.REPORTS_DIR.mkdir(exist_ok=True)
    # CVs for new vacancies with a high Fit — the "📄 CV" link in the HTML report
    if cvgen.AUTO_MAX > 0:
        progress.start("cv")
        cvgen.auto_build(ctx.reported, today)
    else:
        progress.skip("cv", "auto-build turned off: CVs are built with the button in the report")
    progress.start("reports")
    out = config.REPORTS_DIR / f"jobs_{today.isoformat()}.md"
    out.write_text(report.render(today, fresh, stats, tg_jobs), encoding="utf-8")
    page = html_report.render(today, fresh, stats, tg_jobs)
    html_out = config.REPORTS_DIR / f"jobs_{today.isoformat()}.html"
    html_out.write_text(page, encoding="utf-8")
    (config.REPORTS_DIR / "latest.html").write_text(page, encoding="utf-8")  # always the fresh report at one address
    # report vacancies with their text — for a CV per vacancy (cvgen.py, the "📄 CV" button in the report);
    # a copy by date — so CVs can be built from older reports too
    jobs_json = json.dumps([
        {"url": j.url or getattr(j, "post_url", ""), "company": j.company, "title": j.title,
         "location": j.location, "text": getattr(j, "description", "") or getattr(j, "text", ""), "fit": j.cv_fit,
         # for the Companies page: industry, remote, salary (industry.market_by_industry)
         "industries": j.industries, "remote": getattr(j, "remote", None), "salary": getattr(j, "salary", "")}
        for j in ctx.reported
    ], ensure_ascii=False)
    atomic_write_text(config.ROOT / "state" / "jobs_latest.json", jobs_json)
    config.JOBS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(config.JOBS_ARCHIVE_DIR / f"{today.isoformat()}.json", jobs_json)
    progress.count(rejected=len(ctx.rejected), new_today=sum(j.is_new_today for j in ctx.reported))
    log.info("Done: %d new of %d matching -> %s and %s", len(fresh), len(ctx.matched), out, html_out)


if __name__ == "__main__":
    main()

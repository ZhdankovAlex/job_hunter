"""A company's industry from vacancy text (company name, title, description).

Keyword rules, like the dictionaries in cv_match: each industry has patterns with a weight.
Weight 2 is a strong signal (one is enough), 1 is a weak one (two different ones are needed). A match in the
company name or the vacancy title counts double. The threshold is THRESHOLD. A vacancy can have several
industries; if none scored enough — ["other"].

False positives guarded against: "bet" inside words (between, alphabet) — \\b-like boundaries only and no bare
"bet"; "game" in gamification — whole words and phrases only; "bank" in "data bank" / "food bank"; "insurance"
and "medical" from a list of benefits (health insurance, medical, dental); "travel" from "travel required";
"payments" in any online shop — a weak signal.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

INDUSTRIES = ["fintech", "trading", "igaming", "crypto", "adtech", "devtools", "security", "ecommerce", "travel",
              "edtech", "healthtech", "gamedev", "ai", "b2b-saas", "bigtech", "outsource"]
LABELS = {
    "fintech": "Banks / fintech", "trading": "Trading / brokers", "igaming": "iGaming / betting",
    "crypto": "Crypto / web3", "adtech": "Adtech / martech", "devtools": "Devtools / infrastructure",
    "security": "Security", "ecommerce": "E-commerce", "travel": "Travel", "edtech": "Edtech",
    "healthtech": "Healthtech", "gamedev": "Gamedev", "ai": "AI / LLM", "b2b-saas": "B2B SaaS",
    "bigtech": "Big Tech / corporations", "outsource": "Outsourcing / staffing", "other": "Other",
}
THRESHOLD = 2

# patterns: (regex, weight); regexes without flags — re.I is added when they are compiled
_RULES: dict[str, list[tuple[str, int]]] = {
    "igaming": [
        (r"i-?gaming", 2), (r"casinos?", 2), (r"sports?\s?books?", 2), (r"(?:sports )?betting", 2),
        (r"bookmakers?", 2), (r"gambling", 2), (r"slot (?:games?|machines?|providers?)", 2), (r"online slots", 2),
        (r"poker", 2), (r"lotter(?:y|ies)", 2), (r"wager(?:ing|s)?", 2), (r"bettors?", 2),
        (r"responsible gaming", 2), (r"live dealer", 2), (r"(?:online|real[- ]money) gaming", 2),
        (r"odds", 1), (r"punters?", 2), (r"jackpots?", 1),
    ],
    "fintech": [
        # "banking" / "financial services" often appear in a list of clients ("customers in banking, …") —
        # weak on their own; strong ones are about the product itself
        (r"fin-?tech", 2), (r"neo-?banks?", 2), (r"(?<!data )(?<!food )(?<!memory )(?<!blood )banks?", 1),
        (r"(?:digital|online|mobile|challenger|retail|core|open) bank(?:ing)?", 2),
        (r"banking", 1), (r"lending", 2), (r"loans?", 1), (r"credit cards?", 2), (r"payment (?:processing|gateway|"
         r"platform|provider|infrastructure|solutions?|services?|systems?)", 2), (r"payments?", 1),
        (r"acquiring", 2), (r"insurtech", 2), (r"wealth management", 2),
        (r"financial (?:services|institutions?|products?|technology)", 1), (r"kyc", 1), (r"aml", 1),
        (r"psp", 1), (r"money transfers?", 2), (r"remittances?", 2), (r"invoic(?:es|ing)", 1),
    ],
    # brokers, forex, prop trading — apart from banks
    "trading": [
        (r"brokerage", 2), (r"(?:forex|fx|cfd|online) (?:broker|brokers|trading)", 2), (r"forex", 2), (r"cfds?", 2),
        (r"trading (?:platform|systems?|firm|engine|terminal|infrastructure)", 2), (r"prop(?:rietary)? trading", 2),
        (r"market making|market maker", 2), (r"(?:algorithmic|algo|high[- ]frequency|hft) trading|hft", 2),
        (r"traders?", 1), (r"trading", 1), (r"order books?", 1), (r"low[- ]latency", 1), (r"exchanges?", 1),
    ],
    "crypto": [
        (r"crypto(?:currenc(?:y|ies))?", 2), (r"blockchains?", 2), (r"web3", 2), (r"defi", 2), (r"nfts?", 2),
        (r"bitcoin", 2), (r"ethereum", 2), (r"solana", 2), (r"on-?chain", 2), (r"stablecoins?", 2),
        (r"smart contracts?", 2), (r"(?:crypto|digital asset) exchange", 2), (r"digital assets?", 1),
        (r"wallets?", 1), (r"tokenomics", 2),
    ],
    "adtech": [
        (r"ad-?tech", 2), (r"mar-?tech", 2), (r"programmatic", 2), (r"real[- ]time bidding|rtb", 2),
        (r"ad (?:network|serving|exchange|platform|campaigns?)s?", 2), (r"advertis(?:ing|ers?)", 1),
        (r"dsp|ssp", 1), (r"affiliate (?:marketing|network|platform)", 2), (r"affiliates?", 1),
        (r"marketing (?:automation|technology|platform|analytics)", 2), (r"attribution", 1),
        (r"publishers?", 1),
    ],
    "devtools": [
        (r"developer tools?", 2), (r"developer (?:platform|experience|productivity)", 1), (r"devtools", 2),
        (r"observability (?:company|vendor)", 2), (r"(?:for|by) developers", 1),
        (r"open[- ]source (?:database|project|company|community|software)", 1),
        (r"(?:database|data infrastructure|cloud infrastructure|infrastructure software) (?:company|provider|vendor)", 2),
        (r"used by (?:millions of |thousands of )?developers", 2), (r"api platform", 1), (r"sdks?", 1),
        (r"real-time analytics database|analytical database|time[- ]series database", 2),
    ],
    "security": [
        (r"cyber-?security", 2), (r"cyber security", 2), (r"security (?:company|platform|products?|vendor)", 2),
        (r"threat (?:detection|intelligence|hunting)", 2), (r"endpoint (?:security|protection|detection)", 2),
        (r"siem", 2), (r"malware", 2), (r"vulnerability (?:management|research|scanning)", 2), (r"zero[- ]trust", 1),
        (r"identity (?:and access )?management", 1), (r"penetration testing|pentest\w*", 1),
    ],
    "ecommerce": [
        (r"e-?commerce", 2), (r"online (?:store|retail(?:er)?|shop(?:ping)?|marketplace)", 2), (r"marketplaces?", 1),
        (r"retail(?:ers?)?", 1), (r"shoppers?", 1), (r"merchants?", 1), (r"checkout", 1),
        (r"(?:food|grocery|quick) delivery|q-?commerce", 2), (r"sellers?", 1), (r"e-?com", 1),
    ],
    "travel": [
        (r"travel(?:lers?|ers?)?(?! (?:is )?(?:required|expenses|occasionally|up to|to (?:the )?office))", 1),
        (r"travel (?:tech|industry|company|platform|booking)", 2), (r"hotels?", 1), (r"flight (?:search|bookings?|tickets?)", 2),
        (r"airlines?", 2), (r"tourism", 2), (r"hospitality", 1), (r"vacation rentals?", 2),
        (r"(?:hotel|flight|travel) bookings?", 2),
    ],
    "edtech": [
        (r"ed-?tech", 2), (r"e-?learning", 2), (r"online (?:learning|education|courses?|school)", 2),
        (r"learning (?:platform|management system)", 2), (r"students?", 1), (r"teachers?|tutors?|tutoring", 1),
        (r"educational (?:platform|technology|products?)", 2), (r"(?:language|coding) learning", 2),
    ],
    "healthtech": [
        (r"health-?tech", 2), (r"digital health", 2),
        (r"healthcare (?:company|industry|platform|providers?|technology|organi[sz]ations?|systems?)", 2),
        (r"healthcare", 1), (r"tele-?(?:health|medicine)", 2),
        (r"clinical", 1), (r"patients?", 1), (r"hospitals?", 1), (r"medtech|biotech", 2), (r"pharma\w*", 1),
        (r"mental health (?:platform|app|care)", 2),
    ],
    "gamedev": [
        (r"game (?:studio|studios|developer|development|engine|designers?|servers?|backend|industry)", 2),
        (r"video ?games?", 2), (r"(?:mobile|pc|console|online|multiplayer) games?", 2), (r"gamers?", 1),
        (r"free-to-play|f2p", 2), (r"mmo(?:rpg)?", 2), (r"live ?ops", 1), (r"multiplayer", 1), (r"unreal engine", 2),
        (r"games?", 1), (r"(?<!team )players?", 1),
    ],
    # AI companies and AI products: strong ones are about the company itself; LLMs / agents alone are weak —
    # "experience with LLM tools is a plus" shows up in any vacancy
    "ai": [
        (r"ai (?:research|safety) (?:company|lab|organi[sz]ation)", 2),
        (r"ai[- ](?:first|native) (?:product |company|startup|platform)", 2),
        (r"generative ai (?:company|startup|platform|products?)", 2), (r"foundation models?", 2),
        (r"frontier (?:ai|models?)", 2), (r"(?:ai|llm|agent) platform", 1), (r"large language models?|llms?", 1),
        (r"generative ai|gen-?ai", 1), (r"ai[- ]agents?|agentic", 1),
    ],
    "b2b-saas": [
        (r"saas", 2), (r"b2b", 2), (r"enterprise (?:customers|clients|software|saas)", 1),
        (r"subscription", 1), (r"crm", 1), (r"erp", 1),
    ],
    "outsource": [
        (r"outsourc\w+", 2), (r"out-?staff\w*", 2), (r"(?:custom )?software development (?:company|services|agency)", 2),
        (r"it (?:services|consulting) company", 2), (r"(?:for|on behalf of) (?:our|a) client", 2),
        (r"our clients?(?:'s)? (?:projects?|teams?)", 1), (r"nearshore|offshore", 1), (r"consultanc(?:y|ies)", 1),
        (r"digital agency|software agency", 2),
        # staffing agencies and hiring platforms — a vacancy "on behalf of a client"
        (r"on behalf of (?:a|our) (?:partner|client)(?: company)?", 2), (r"talent pool", 1),
        (r"(?:staffing|recruit(?:ment|ing)|hiring) (?:agency|company|partner)", 2),
        (r"for an? international (?:institution|client)", 2), (r"(?:it|software) (?:services|consulting)", 1),
    ],
}

# Well-known companies whose industry is known in advance (a whole-word match on the company name): their
# descriptions often list the industries of their clients, which are not the company's own. A few examples;
# your own — in settings.toml ([industry_companies]) or in the 4th field of companies.txt.
BUILTIN_COMPANIES: dict[str, list[str]] = {
    "fintech": ["Stripe", "Adyen", "Wise", "Klarna", "Monzo"],
    "crypto": ["Coinbase", "Kraken", "Ledger"],
    "devtools": ["GitLab", "Grafana", "Datadog", "ClickHouse", "Vercel", "JetBrains", "Sentry"],
    "security": ["CrowdStrike", "1Password", "Snyk"],
    "ecommerce": ["Shopify", "Zalando", "Etsy"],
    "travel": ["Booking.com", "Airbnb", "Skyscanner"],
    "edtech": ["Duolingo", "Coursera"],
    "gamedev": ["Unity", "Epic Games", "Supercell"],
    "ai": ["Anthropic", "OpenAI", "Hugging Face"],
    "bigtech": ["Amazon", "AWS", "Microsoft", "Google", "Apple", "Meta", "NVIDIA", "IBM", "Oracle", "SAP"],
    "outsource": ["EPAM", "Toptal", "GlobalLogic"],
}


def _settings_companies() -> dict[str, list[str]]:
    """[industry_companies] from settings.toml: {industry code: [company, …]}, empty by default."""
    from . import geo

    raw = geo.S.get("industry_companies", {})
    return {ind: [str(n) for n in names] for ind, names in raw.items() if ind in LABELS and isinstance(names, list)}


SETTINGS_COMPANIES = _settings_companies()
COMPANIES: dict[str, list[str]] = {
    ind: list(dict.fromkeys([*BUILTIN_COMPANIES.get(ind, []), *SETTINGS_COMPANIES.get(ind, [])]))
    for ind in INDUSTRIES if BUILTIN_COMPANIES.get(ind) or SETTINGS_COMPANIES.get(ind)}


def _rx(body: str) -> re.Pattern[str]:
    # word boundaries (a hyphen on the right ends nothing: "game-changer" is not a game): "bet" is not found in
    # "between", "game" — in "gamification"
    return re.compile(rf"(?<!\w)(?:{body})(?!\w|-[a-z0-9])", re.I)


def _combined(rules: list[tuple[str, int]]) -> tuple[re.Pattern[str], dict[str, int]]:
    """All patterns of an industry as one regex with named groups g0, g1, … -> (regex, {group: weight}).

    One pass over the text instead of one per pattern (~150 patterns × a long description is ~16 ms per vacancy).
    Strong patterns go first: when two match at the same position, the strong one counts.
    """
    ordered = sorted(rules, key=lambda r: -r[1])
    body = "|".join(f"(?P<g{i}>{rx})" for i, (rx, _) in enumerate(ordered))
    return _rx(body), {f"g{i}": w for i, (_, w) in enumerate(ordered)}


RULES_RX: dict[str, tuple[re.Pattern[str], dict[str, int]]] = {ind: _combined(rules) for ind, rules in _RULES.items()}
COMPANY_RX: dict[str, re.Pattern[str]] = {
    ind: _rx("|".join(re.escape(n) for n in names)) for ind, names in COMPANIES.items()}


class Listed:
    """Marks in companies.txt (4th field — industries, 5th — brands): who owns a name or a brand.

    The company name and its brands in a vacancy's "company" field — an exact match ignoring case and punctuation;
    brands in the text of a post — a whole word, case-sensitive (only for companies not on the list: "NDA",
    staffing agencies, Telegram posts), so that a product name in the text ties the vacancy to its owner.
    """

    def __init__(self, companies: Iterable[Any]) -> None:
        from .models import norm_name

        self.by_key: dict[str, tuple[str, list[str]]] = {}  # norm_name(name|brand) -> (name, industries)
        self.names: list[str] = []  # marked companies (with an industry) — for "Companies" without vacancies
        brands: dict[str, str] = {}
        for c in companies:
            inds = [i for i in c.industries if i in LABELS]
            self.by_key.setdefault(norm_name(c.name), (c.name, inds))
            if inds:
                self.names.append(c.name)
            for b in c.brands:
                self.by_key.setdefault(norm_name(b), (c.name, inds))
                if len(b) >= 4:  # short ones (SIG, HRT) are too ambiguous in a text
                    brands[b] = c.name
        self._brand_owner = brands
        self._brand_rx = re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(b) for b in sorted(brands, key=len, reverse=True)) + r")(?!\w)"
        ) if brands else None

    def owner(self, company: str, text: str = "") -> str:
        """The name of the listed company the vacancy belongs to, or ''."""
        from .models import norm_name

        hit = self.by_key.get(norm_name(company or ""))
        if hit:
            return hit[0]
        m = self._brand_rx.search(f"{company}\n{text}") if self._brand_rx else None
        return self._brand_owner[m.group(0)] if m else ""

    def industries(self, name: str) -> list[str]:
        from .models import norm_name

        hit = self.by_key.get(norm_name(name or ""))
        return hit[1] if hit else []


_LISTED: tuple[float, Listed] | None = None


def listed() -> Listed:
    """Marks from companies.txt; re-read when the file changes (the web server lives long)."""
    global _LISTED
    from . import companies, config

    path = config.COMPANIES_FILE
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if _LISTED is None or _LISTED[0] != mtime:
        _LISTED = (mtime, Listed(companies.load_companies(path) if path.exists() else []))
    return _LISTED[1]


def known_industries(company: str, marks: Listed | None = None) -> list[str]:
    """Industries of a company known in advance: COMPANIES and companies.txt marks (in INDUSTRIES order)."""
    marked = set((marks or listed()).industries(company or ""))
    return [ind for ind in INDUSTRIES
            if ind in marked or (ind in COMPANY_RX and COMPANY_RX[ind].search(company or ""))]


def scores(company: str, title: str, text: str) -> dict[str, int]:
    """Industry scores: the sum of weights of different matched patterns; in the company/title — double."""
    head = f"{company}\n{title}"
    out: dict[str, int] = {}
    for ind, (rx, weights) in RULES_RX.items():
        in_head = {m.lastgroup for m in rx.finditer(head)}
        in_text = {m.lastgroup for m in rx.finditer(text)} - in_head
        total = sum(2 * weights[g] for g in in_head if g) + sum(weights[g] for g in in_text if g)
        if ind in COMPANY_RX and COMPANY_RX[ind].search(company):
            total += 2 * THRESHOLD
        if total:
            out[ind] = total
    return out


def classify(company: str, title: str = "", text: str = "", marks: Listed | None = None) -> list[str]:
    """Industries of a vacancy in INDUSTRIES order; ["other"] if none reached THRESHOLD."""
    # a known company — its industry from COMPANIES / companies.txt: a description often lists the industries
    # of the clients ("we serve fintech, crypto and gaming"), which are not the company's own;
    # a post without a company ("NDA") under a brand from companies.txt — the industry of the brand's owner
    marks = marks or listed()
    known = known_industries(company, marks) or known_industries(marks.owner(company, f"{title}\n{text}"), marks)
    if known:
        return known
    found = scores(company or "", title or "", text or "")
    picked = [ind for ind in INDUSTRIES if found.get(ind, 0) >= THRESHOLD]
    # "games" at a casino are iGaming, not gamedev: gamedev stays only if it scored higher
    if "igaming" in picked and "gamedev" in picked and found["gamedev"] <= found["igaming"]:
        picked.remove("gamedev")
    return picked or ["other"]


BOARD_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}", "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}", "workable": "https://apply.workable.com/{slug}",
    "teamtailor": "https://{slug}.teamtailor.com/jobs", "recruitee": "https://{slug}.recruitee.com",
    "bamboohr": "https://{slug}.bamboohr.com/careers", "breezy": "https://{slug}.breezy.hr",
}


def board_links(mapping: dict[str, dict[str, Any]]) -> dict[str, str]:
    """{norm_name(company): link to the public vacancies page} from ats_mapping.json."""
    from .models import norm_name

    return {norm_name(name): BOARD_URLS[e["platform"]].format(slug=e["slug"])
            for name, e in mapping.items() if e.get("platform") in BOARD_URLS and e.get("slug")}


COMPANY_SHARE = 0.4  # a company's industry is one that at least this share of its vacancies has


def companies_overview(jobs: Iterable[dict[str, Any]], status: Callable[[str], str],
                       mapping: dict[str, dict[str, Any]], marks: Listed | None = None) -> list[dict[str, Any]]:
    """Companies summary: industries, vacancies over all days, the last day, application status, board.

    jobs — archive records with day / industries fields (as in web.companies_report); vacancies with the same
    title at one company count once (duplicates from different sources). A vacancy under a brand from
    companies.txt counts for the brand's owner. Plus companies from [industry_companies] in settings.toml and
    those marked in companies.txt that have no vacancies — to search by hand. status(company) — the
    application status or ''.
    """
    from .models import norm_name

    marks = marks or listed()
    boards = board_links(mapping)
    by_key: dict[str, dict[str, Any]] = {}
    for job in jobs:
        company = marks.owner(job.get("company", ""), f"{job.get('title', '')}\n{job.get('text', '')}") \
            or job.get("company", "")
        key = norm_name(company)
        if not key:
            continue
        row = by_key.setdefault(key, {"names": Counter(), "titles": {}, "ind": Counter()})
        row["names"][company] += 1
        title = norm_name(job.get("title", ""))
        if title not in row["titles"] or job.get("day", "") > row["titles"][title]["day"]:
            row["titles"][title] = {"title": job.get("title", ""), "url": job.get("url", ""), "day": job.get("day", "")}
        row["ind"].update(set(job.get("industries") or ["other"]))
        row["ind"]["_jobs"] += 1  # an industry's share is of the number of vacancies, not of labels
    for name in [*(n for names in SETTINGS_COMPANIES.values() for n in names), *marks.names]:
        row = by_key.setdefault(norm_name(name), {"names": Counter(), "titles": {}, "ind": Counter()})
        row["names"][name] += 0
    out = []
    for key, row in by_key.items():
        name = row["names"].most_common(1)[0][0]
        known = known_industries(name, marks)
        n = len(row["titles"])
        total = row["ind"]["_jobs"] or 1
        guessed = [ind for ind in INDUSTRIES if row["ind"][ind] / total >= COMPANY_SHARE] if n else []
        industries = known or guessed or ["other"]
        jobs_list = sorted(row["titles"].values(), key=lambda j: j["day"], reverse=True)
        out.append({"name": name, "industries": industries, "by": "list" if known else "vacancies",
                    "vacancies": n, "last": jobs_list[0]["day"] if jobs_list else "", "status": status(name),
                    "board": boards.get(key, ""), "jobs": jobs_list})
    # first those with vacancies and no application, then applied, then without vacancies — search by hand
    out.sort(key=lambda c: (c["vacancies"] == 0, bool(c["status"]), -c["vacancies"], c["name"].lower()))
    return out


TOP_SKILLS = 6
MIN_SALARIES = 3  # fewer salary ranges — no median: noise


def market_by_industry(jobs: Iterable[dict[str, Any]], marks: Listed | None = None) -> list[dict[str, Any]]:
    """The market by industry: vacancies (no repeats by company and title), remote share, median salary
    (EUR/month, middle of the range), the most frequent technologies.

    jobs — archive records with industries / remote / salary fields (as in companies_overview);
    the remote share is among vacancies whose format is known.
    """
    import statistics

    from . import salary
    from .cv_match import TECH_RX
    from .models import norm_name

    marks = marks or listed()
    seen: set[tuple[str, str]] = set()
    rows: dict[str, dict[str, Any]] = {}
    for job in jobs:
        company = marks.owner(job.get("company", ""), f"{job.get('title', '')}\n{job.get('text', '')}") \
            or job.get("company", "")
        key = (norm_name(company), norm_name(job.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        pay = salary.parse(job.get("salary") or "")
        text = f"{job.get('title', '')}\n{job.get('text', '')}"
        techs = {name for name, rx in TECH_RX.items() if rx.search(text)}
        for ind in set(job.get("industries") or ["other"]):
            row = rows.setdefault(ind, {"vacancies": 0, "remote": 0, "known_remote": 0, "pay": [], "skills": Counter()})
            row["vacancies"] += 1
            if job.get("remote") is not None:
                row["known_remote"] += 1
                row["remote"] += bool(job["remote"])
            if pay:
                row["pay"].append((pay[0] + pay[1]) / 2)
            row["skills"].update(techs)
    out = []
    for ind in [*INDUSTRIES, "other"]:
        if ind not in rows:
            continue
        row = rows[ind]
        n = row["vacancies"]
        out.append({
            "industry": ind, "label": LABELS.get(ind, ind), "vacancies": n,
            "remote_share": round(100 * row["remote"] / row["known_remote"]) if row["known_remote"] else None,
            "salaries": len(row["pay"]),
            "salary_median": round(statistics.median(row["pay"]), -2) if len(row["pay"]) >= MIN_SALARIES else None,
            "skills": [{"name": s, "share": round(100 * c / n)} for s, c in row["skills"].most_common(TOP_SKILLS)],
        })
    out.sort(key=lambda r: -r["vacancies"])
    return out

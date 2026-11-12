"""All settings in one place: paths, filters, keywords."""
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_or_example(path: Path) -> Path:
    """The personal file (not in the repo) or, until it exists, the example next to it: settings.example.toml."""
    if path.exists():
        return path
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    return example if example.exists() else path

COMPANIES_FILE = local_or_example(ROOT / "companies.txt")  # no own list — companies.example.txt
MAPPING_FILE = ROOT / "ats_mapping.json"  # cache of found ATS boards, created automatically
SEEN_FILE = ROOT / "state" / "seen.json"
REPORTS_DIR = ROOT / "reports"
ERRORS_LOG = ROOT / "errors.log"  # warnings and errors of the current run
ERRORS_LOG_PREV = ROOT / "errors.prev.log"  # of the previous run (overwritten by every new run)
STAFFING_FILE = local_or_example(ROOT / "staffing_blacklist.txt")
# no own file — settings.example.toml / profile.example.toml. JOBHUNTER_SETTINGS points to another file (tests use
# the example so they do not depend on personal settings); JOBHUNTER_PROFILE and JOBHUNTER_CV_BASE do the same
SETTINGS_FILE = Path(os.environ.get("JOBHUNTER_SETTINGS") or local_or_example(ROOT / "settings.toml"))
PROFILE_FILE = Path(os.environ.get("JOBHUNTER_PROFILE") or local_or_example(ROOT / "profile.toml"))
# Mailbox with recruiter letters (applications.py): Google OAuth client and local token/cache
GMAIL_CLIENT_FILE = ROOT / "gmail_client.json"
GMAIL_TOKEN_FILE = ROOT / "state" / "gmail_token.json"
MAIL_CACHE_FILE = ROOT / "state" / "mail_cache.json"
JOBS_ARCHIVE_DIR = ROOT / "state" / "jobs"  # vacancies of every report with their text: <date>.json
MAIL_SYNC_FILE = ROOT / "state" / "mail_sync.json"  # when mail was last updated and how the last attempt ended
# applications not from mail (Telegram, LinkedIn): added by hand, merged with the letters
MANUAL_APPLICATIONS_FILE = ROOT / "applications_manual.toml"

# CV base for the generator (phone, email — personal): no own file — cv/base.example.toml
CV_BASE_FILE = Path(os.environ.get("JOBHUNTER_CV_BASE") or local_or_example(ROOT / "cv" / "base.toml"))

_SETTINGS = tomllib.loads(SETTINGS_FILE.read_text(encoding="utf-8"))

# --- Home country: settings.toml [home] -------------------------------------
# Vacancies there (office, hybrid, remote) are shown on a par with worldwide remote. Empty — remote only.
_HOME = _SETTINGS.get("home", {})
HOME_COUNTRY = str(_HOME.get("country", "")).strip()
HOME_ISO = str(_HOME.get("iso", "")).strip().upper()  # ISO country code for Himalayas / freehire queries
_HOME_NAMES = [n for n in [HOME_COUNTRY, *_HOME.get("places", [])] if str(n).strip()]
_NEVER = r"(?!x)x"  # a regex that never matches (no home country set)

USER_AGENT = "job-hunter/1.0 (open-source job search script; 1 req/s)"
MIN_INTERVAL = 1.0  # seconds between requests to one host
# Workable answers 429 even at 1 req/s, so it gets a longer pause
HOST_INTERVALS = {"apply.workable.com": 6.0, "t.me": 2.0}

# Public Telegram channels (web preview t.me/s/...): settings.toml [telegram], off by default
_TELEGRAM = _SETTINGS.get("telegram", {})
TELEGRAM_ENABLED = bool(_TELEGRAM.get("enabled", False))
TELEGRAM_CHANNELS = list(_TELEGRAM.get("channels", []))
TELEGRAM_MAX_AGE_DAYS = int(_TELEGRAM.get("max_age_days", 60))
# Channels of the Russian-speaking market: their salary ranges are a separate group in the salary base (salary.py)
TELEGRAM_CIS_CHANNELS = set(_TELEGRAM.get("cis_channels", []))
TELEGRAM_MAX_PAGES = 25  # guard against endless paging
WORKERS = 8 # threads are limited by the per-host rate, not by each other

# After how many days to search again for a slug of companies not found on any ATS
RECHECK_NOT_FOUND_DAYS = 30

ARBEITNOW_PAGES = 5

# Himalayas: robots.txt disallows &page=, so instead of paging —
# several narrow queries, one (fresh) page each.
HIMALAYAS_QUERIES = [
    {"q": "python backend", "worldwide": "true"},
    {"q": "python", "worldwide": "true"},
    {"q": "django", "worldwide": "true"},
    {"q": "fastapi", "worldwide": "true"},
    {"q": "backend engineer", "worldwide": "true"},
]
if HOME_ISO:  # plus vacancies in the home country
    HIMALAYAS_QUERIES += [{"q": "python", "country": HOME_ISO}, {"q": "backend", "country": HOME_ISO}]

I = re.IGNORECASE

# --- Keywords ---------------------------------------------------------------
PYTHON_RX = re.compile(r"\b(?:python|django|flask|fastapi)\b", I)
KEYWORD_RX = re.compile(
    r"\b(?:python|django|flask|fastapi|postgres(?:ql)?|back[\s-]?end)\b", I
)

# The title must look like an engineering role (otherwise "backend" in the text
# of a sales vacancy passes the filter too).
TITLE_ROLE_RX = re.compile(
    r"engineer|developer|programmer|back[\s-]?end|python|django|architect|"
    r"tech(?:nical)? lead|\bswe\b|\bsde\b|разработчик|программист",
    I,
)
TITLE_EXCLUDE_RX = re.compile(
    r"front[\s-]?end|\bios\b|android|mobile|react native|\bintern(?:ship)?\b|"
    r"\bjunior\b|graduate|\b(?:sales|solutions?|support|customer|field|"
    r"forward deployed|hardware|mechanical|electrical) engineer|"
    r"\bqa\b|\bsdet\b|test engineer|designer|recruiter",
    I,
)

# --- Location ---------------------------------------------------------------
HOME_RX = re.compile(
    "|".join(rf"(?<!\w){re.escape(n)}(?!\w)" for n in _HOME_NAMES) or _NEVER, I
)
REMOTE_RX = re.compile(r"remote|work from home|\bwfh\b|anywhere|world[\s-]?wide|distributed|удал[её]н", I)
BROAD_RX = re.compile(
    r"world[\s-]?wide|global|anywhere|\bemea\b|europe|\beu\b|\bcis\b|international|"
    r"европ|по всему миру|весь мир|где угодно|\bснг\b|"
    r"\bcet\b|\beet\b|(?:utc|gmt)\s?[+±-]?\s?[0-3]\b",
    I,
)
# Regions that never fit (unless a BROAD marker is next to them)
NON_EU_RX = re.compile(
    r"\b(?:usa?|u\.s\.a?\.?|united states|canada|north america|americas?|"
    r"latam|latin america|apac|asia|india|australia|new zealand|brazil|mexico|"
    r"argentina|colombia|chile|peru|japan|singapore|china|hong kong|korea|"
    r"philippines|vietnam|thailand|indonesia|malaysia|pakistan|israel|uae|"
    r"dubai|saudi|africa|nigeria|kenya|egypt|ghana|"
    r"new york|nyc|san francisco|sf|seattle|austin|boston|chicago|denver|"
    r"los angeles|toronto|vancouver|bangalore|bengaluru|sydney|tokyo)\b",
    I,
)
LOCATION_NOISE_RX = re.compile(
    r"remote(?:ly)?|fully|100%|work from home|\bwfh\b|home office|only|based|"
    r"hybrid|\bor\b|[\s,;:()/\-–—|·•]",
    I,
)

# --- Language ---------------------------------------------------------------
# "Primary" languages that conflict with a Python profile.
OTHER_LANGS = {
    "Java": re.compile(r"\bjava\b(?!\s*script)", I),
    "Kotlin": re.compile(r"\bkotlin\b", I),
    "Go": re.compile(
        r"(?i:\bgolang\b)|\bGo\b(?=\s*(?:,|/|\)|and\b|or\b|developer|engineer|"
        r"services?|experience|programming|language|backend))|"
        r"(?<=in )Go\b|(?<=[,/] )Go\b"
    ),
    "Ruby": re.compile(r"\bruby\b|\brails\b", I),
    "Scala": re.compile(r"\bscala\b", I),
    "PHP": re.compile(r"\bphp\b|\blaravel\b", I),
    "C#/.NET": re.compile(r"\bc#|\.net\b", I),
}
NICE_TO_HAVE_RX = re.compile(
    r"nice[\s-]to[\s-]have|bonus|\ba plus\b|is a plus|preferred|desirable|"
    r"advantage|optional|familiarity with|будет плюсом|желательно",
    I,
)
REQUIRED_RX = re.compile(
    r"requirements|qualifications|must[\s-]have|what you(?:'ll| will)? (?:need|bring)|"
    r"you have|требования",
    I,
)

# --- Telegram --------------------------------------------------------------
TG_KEYWORD_RX = re.compile(
    r"python|back[\s-]?end|б[эе]к[\s-]?енд|fastapi|django|postgres", I
)
# IT roles only
TG_ROLE_RX = re.compile(
    r"engineer|developer|programmer|back[\s-]?end|б[эе]к[\s-]?енд|python|django|architect|"
    r"devops|\bsre\b|tech(?:nical)? lead|team ?lead|тимлид|разработчик|программист|инженер|архитектор",
    I,
)
TG_EXCLUDE_RX = re.compile(
    TITLE_EXCLUDE_RX.pattern + r"|\btrainee\b|стаж[её]р|джун|младш|начинающ|автотест|аналитик|фронт[\s-]?енд|тестировщик|c\+\+|\b1[сc]\b|администратор|системный программист",
    I,
)

# --- Language in the role TITLE (ATS and Telegram) --------------------------
TITLE_LANGS = {
    "Go": re.compile(r"(?i:\bgolang\b)|\bGo\b(?!-to)"),
    "Java": re.compile(r"\bjava\b(?!\s*script)", I),
    "Kotlin": re.compile(r"\bkotlin\b", I),
    "Ruby": re.compile(r"\bruby\b|\brails\b", I),
    ".NET": re.compile(r"(?<![a-z0-9])\.net\b|\bdotnet\b|\bc#", I),
    "PHP": re.compile(r"\bphp\b|\blaravel\b", I),
    "Scala": re.compile(r"\bscala\b", I),
    "Node.js": re.compile(r"\bnode(?:\.?js)?\b", I),
    "Rust": re.compile(r"\brust\b", I),
    "C++": re.compile(r"\bc\+\+", I),
}

# --- Staffing / Top ---------------------------------------------------------
CONTRACT_TITLE_RX = re.compile(r"\bcontract(?:or)?\b", I)
TOP_LIMIT = 15
TOP_SENIORITY_EXCLUDE_RX = re.compile(r"\b(?:head|director|vp|vice president|principal)\b", I)
PRIORITY_MAX_ROWS = 60  # rows in the ✅ section, the rest go to the Appendix

# The 🌍 section: the role must be an engineering one and about Python
RELOC_EXCLUDE_TITLE_RX = re.compile(
    r"value engineer|solutions? (?:architect|engineer)|deployed (?:engineer|architect)|deployment|"
    r"(?:delivery|engineering|product|program|project) manager|\baccount\b|\bsales\b|customer|"
    r"recruit\w*|designer|consult\w*",
    I,
)
RELOC_NEEDS_PYTHON_TITLE_RX = re.compile(
    r"back[\s-]?end|software|developer|platform|\bdata\b|\bml\b|machine learning|\bai\b|engineer", I
)
TOP_PER_COMPANY = 2  # so the top is not made of one company
TOP_TITLE_RX = re.compile(r"(?i:python|back[\s-]?end)|\bAI\b")
TOP_LOCATION_RX = re.compile(
    r"world[\s-]?wide|global|anywhere|\bemea\b|" + HOME_RX.pattern, I
)

# --- Role filter for ALL sections -------------------------------------------
ROLE_EXCLUDE_RX = re.compile(
    r"\bhpc\b|network|solutions? architect|"
    r"advocate|recruit\w*|\bsales\b|support|manager|director|\bhead of\b|designer|\bqa\b|"
    r"quality assurance|тестировщик|менеджер|руководител",
    I,
)
# Backend / Software Engineer / Python — any key in the text is enough
ROLE_CORE_RX = re.compile(
    r"back[\s-]?end|б[эе]к[\s-]?енд|software (?:engineer|developer)|python|django|fastapi|"
    r"разработчик|программист",
    I,
)
# AI/ML Engineer — only if the text mentions python
ROLE_AIML_RX = re.compile(
    r"\b(?:ai|ml|llm|genai|machine learning)\b.*\b(?:engineer|developer)\b|"
    r"\b(?:engineer|developer)\b.*\b(?:ai|ml|llm|machine learning)\b",
    I,
)

# --- USA: state codes, U.S., NAMER/AMER mean the job is tied to the country --
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
US_BOUND_RX = re.compile(
    r"(?:[-–—,(/]\s*|\bRemote\s+)(?:" + "|".join(_US_STATES) + r")\b|"
    r"\bU\.S\.?(?![a-z])|\bNAMER\b|\bAMER\b|(?i:\bnorth america\b|\bamericas\b)"
)
# A "no location tie" marker in the role TITLE — only an explicit region
# ("International"/"Global" in a title is a team name, not a location)
TITLE_BROAD_RX = re.compile(r"world[\s-]?wide|anywhere|\bemea\b|europe|\beu\b", I)
LOCATION_EUROPE_RX = re.compile(r"world[\s-]?wide|global|\bemea\b|europe|\beu\b", I)

# Application status (applications_manual.toml) that hides the company from "New" and the top:
# the company is in an active process (interviews). Russian words are kept for older files.
APPLIED_BLOCK_RX = re.compile(r"собес|интервью|interview", I)
# statuses after which you can apply to the vacancy again: it is shown as usual
RETRY_STATUS_RX = re.compile(r"reject|declin|ignor|no reply|отказ|игнор", I)

# Platform Engineer — an acceptable related role, but only with python
ROLE_PLATFORM_RX = re.compile(
    r"\bplatform\b.*\b(?:engineer|developer)\b|\b(?:engineer|developer)\b.*\bplatform\b", I
)

# --- Employment type: full-time only (employee or contract) ------------------
NOT_FULL_TIME_RX = re.compile(  # "part_time" is how the freehire API writes it
    r"part[\s_-]?time|intern(?:ship)?\b|freelanc\w*|temporary|\btemp\b|hourly|volunteer|per[\s-]diem|"
    r"seasonal|trainee|werkstudent|working student|mini[\s-]?job|"
    r"частичн\w+ занятост\w+|неполн\w+ (?:день|занятост\w+)|фриланс\w*|подработк\w+|стаж[её]р\w*",
    I,
)
# "AI Trainer" — paid-by-the-hour data labelling / model training, not development
NOT_A_JOB_TITLE_RX = re.compile(r"\bai trainer\b|\bannotator\b|data label", I)
HOURLY_PAY_RX = re.compile(r"[$€£]\s?\d+(?:[.,]\d+)?(?:\s?[-–]\s?[$€£]?\d+(?:[.,]\d+)?)?\s?(?:/|per|an)\s?(?:hour|hr)\b", I)

# --- Hiding by the vacancy text ---------------------------------------------
# Data labelling / model training with piecework pay — not development
NOT_A_JOB_TEXT_RX = re.compile(r"\bai trainer\b|train AI systems|per[\s-]task\b", I)
# A strict tie to a place / no relocation; not hidden if the home country or EU is next to it
RESIDENCY_RX = re.compile(r"must (?:reside|be located|be based|live|be resident) in", I)
# No relocation / visa support — hidden only if the vacancy is tied to another country
NO_SUPPORT_RX = re.compile(
    r"not (?:providing|offering|able to (?:provide|offer)|provide|offer) (?:any )?(?:relocation|visa)|"
    r"no relocation|relocation (?:support |assistance |package )?(?:is |will )?not (?:be )?(?:provided|offered|available)|"
    r"no visa sponsorship|(?:unable|not able) to sponsor|cannot sponsor|can't sponsor|"
    r"do(?:es)? not (?:offer |provide )?(?:visa )?sponsor\w*|without (?:visa )?sponsorship",
    I,
)
RESIDENCY_OK_RX = re.compile(HOME_RX.pattern + r"|\bEU\b|europe|\bemea\b|european union", I)

# --- Penalties to the relevance score ---------------------------------------
GO_REQUIRED_RX = re.compile(
    r"(?:experience|proficien\w+|expert\w*|strong|fluen\w+|years|skills?|knowledge|written)"
    r"[^.\n]{0,40}?(?:\b[Gg]olang\b|\bGo\b(?!-to|\s+(?:to|live|beyond|above)))"
)
JVM_SPRING_RX = re.compile(
    r"\bJVM\b|Java Virtual Machine|Spring Boot|Spring Framework|\bSpring\b(?=\s*(?:,|/|\)|MVC|Cloud|Data|Security))"
)
YEARS_8_PLUS_RX = re.compile(r"\b(?:[89]|1\d)\s?\+\s*(?:years|yrs|лет)|\b(?:[89]|1\d)\s+(?:or more|plus)\s+years", I)

# Priority companies: an engineering role with python in the text, minus explicit exclusions
PRIORITY_ROLE_RX = re.compile(r"engineer|developer|programmer|architect|\bswe\b", I)

# Roles shown with a penalty (unless the title has Backend/Software)
ROLE_PENALTY_RX = re.compile(
    r"data engineer|analytics engineer|data scientist|ml research\w*|research (?:scientist|engineer)",
    I,
)
# Operations roles: the default profile is backend product development, not DevOps/SRE; such
# vacancies are hidden unless the title has Backend / Software Engineer / Developer
OPS_ROLE_RX = re.compile(
    r"devops|devsecops|\bsre\b|site reliability|infra(?:structure)? engineer|security (?:engineer|operations|analyst)|"
    r"cloud engineer|systems? engineer|systems? administrator|sysadmin|dataops|mlops|network engineer|"
    r"production engineer|reliability engineer|observability engineer|release engineer|build engineer",
    I,
)
CORE_TITLE_RX = re.compile(
    r"back[\s-]?end|б[эе]к[\s-]?енд|software (?:engineer|developer)|\bdeveloper\b|python|django|fastapi|"
    r"full[\s-]?stack|product engineer|разработчик|программист",
    I,
)
PLATFORM_TITLE_RX = re.compile(r"platform|infrastructure|\binfra\b|internal tools|developer experience|devex", I)
OTHER_LANGS["Rust"] = re.compile(r"\brust\b", I)

# Sources that give only a title / short preview — the vacancy text counts as unavailable
PREVIEW_ONLY_SOURCES = {"justjoin.it", "remote.com", "remoterocketship", "cryptocurrencyjobs", "bolt"}

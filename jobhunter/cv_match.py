"""Matching a vacancy against the CV: which requirements you meet, what is missing, where there is a mismatch.

CVs (PDF) are set in settings.toml ([cv].files). Their text, read through the technology dictionary,
gives "what the candidate has"; the vacancy text gives "what is required". Technologies from
nice-to-have do not count as a gap, and neither do alternatives in lists like "X, Y or Z"
if one of them is in the CV.
"""
from __future__ import annotations

import json
from collections import Counter
import logging
import re
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

CACHE_FILE = config.ROOT / "state" / "cv_text.json"
_I = re.I

# Technology -> pattern. The names are shown in the report as written here.
TECH = {
    # languages
    "Python": r"\bpython\b", "Go": r"\b[Gg]olang\b|\bGo\b(?=\s*(?:,|/|\)|and\b|or\b|developer|engineer|services?|experience))",
    "Java": r"\bjava\b(?!\s*script)", "Kotlin": r"\bkotlin\b", "Scala": r"\bscala\b", "Rust": r"\brust\b",
    "C++": r"c\+\+", ".NET / C#": r"(?<![\w])\.net\b|\bc#", "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b", "Node.js": r"\bnode(?:\.?js)?\b", "Ruby": r"\bruby\b|\brails\b",
    "PHP": r"\bphp\b", "Elixir": r"\belixir\b", "SQL": r"\bsql\b",
    # frameworks and protocols
    "Django": r"\bdjango\b", "FastAPI": r"fastapi", "Flask": r"\bflask\b", "Tornado": r"\btornado\b",
    "SQLAlchemy": r"sqlalchemy", "Celery": r"\bcelery\b", "asyncio": r"asyncio|aiohttp|async python",
    "Pydantic": r"pydantic", "gRPC": r"\bgrpc\b", "GraphQL": r"graphql", "REST API": r"\brest(?:ful)?\b",
    "WebSocket": r"websockets?", "pytest": r"pytest", "mypy": r"\bmypy\b|type hints|static typing",
    "Jira": r"\bjira\b", "Agile / Scrum": r"\bagile\b|\bscrum\b|\bkanban\b",
    # general concepts that often appear in requirements
    "Distributed systems": r"distributed systems?", "Microservices": r"micro[\s-]?services?",
    "Event-driven architecture": r"event[\s-]+driven", "Observability": r"observability",
    "Encryption": r"encrypt\w*|cryptograph\w*", "NoSQL": r"\bnosql\b", "Git": r"\bgit\b(?![\s-]?(?:lab|hub))",
    "Bash": r"\bbash\b|shell scripting",
    # storage
    "PostgreSQL": r"postgres(?:ql)?", "MySQL": r"\bmysql\b", "MongoDB": r"mongo(?:db)?", "Redis": r"\bredis\b",
    "Cassandra": r"cassandra", "ClickHouse": r"clickhouse", "Elasticsearch / OpenSearch": r"elastic\s?search|opensearch",
    "DynamoDB": r"dynamodb", "SQLite": r"sqlite", "Snowflake": r"\bsnowflake\b", "BigQuery": r"bigquery",
    # queues
    "Kafka": r"\bkafka\b", "RabbitMQ": r"rabbitmq", "SQS / SNS": r"\bsqs\b|\bsns\b", "NATS": r"\bnats\b",
    # infrastructure
    "Docker": r"\bdocker\b", "Kubernetes": r"kubernetes|\bk8s\b", "Helm": r"\bhelm\b", "Terraform": r"terraform",
    "Ansible": r"ansible", "AWS": r"\baws\b|amazon web services", "GCP": r"\bgcp\b|google cloud",
    "Azure": r"\bazure\b", "Linux": r"\blinux\b", "CI/CD": r"ci/cd|gitlab ci|github actions|jenkins|circleci",
    "Prometheus": r"prometheus", "Grafana": r"grafana", "OpenTelemetry": r"opentelemetry|otel\b",
    "Datadog": r"datadog",
    # data
    "Airflow": r"airflow", "Spark": r"\bspark\b", "dbt": r"\bdbt\b", "Flink": r"\bflink\b",
    "ETL": r"\betl\b|\belt\b pipelines?", "Databricks": r"databricks", "Pandas": r"\bpandas\b", "Hadoop": r"hadoop",
    "Redshift": r"redshift", "Data warehouse": r"\bdwh\b|data warehous\w*", "BI tools": r"\bbi\b|tableau|looker|power bi",
    "MLOps": r"\bmlops\b", "Machine learning": r"machine learning", "NLP": r"\bnlp\b|natural language processing",
    "MCP": r"\bmcp\b|model context protocol", "GPU / CUDA": r"\bgpus?\b|\bcuda\b",
    "ELK": r"\belk\b|kibana|logstash", "Argo CD / Workflows": r"\bargo\s?(?:cd|workflows?)?\b",
    "Networking (TCP/IP, DNS)": r"\btcp(?:/ip)?\b|\bdns\b|\bbgp\b|network protocols",
    "HTTP": r"\bhttps?\b(?!s?:)",
    "IAM": r"\biam\b|identity and access", "Security (SIEM, SOC)": r"\bsiem\b|\bsoc ?2?\b", "GDPR": r"\bgdpr\b",
    "Perl": r"\bperl\b", "ERP": r"\berp\b", "ORM": r"\borms?\b",
    # AI
    "LLM": r"\bllms?\b|large language models?", "RAG": r"\brag\b|retrieval[\s-]augmented",
    "Vector search / embeddings": r"embeddings?|vector (?:search|database|store|db)|pgvector|chromadb|pinecone|weaviate|qdrant",
    "LangChain / LlamaIndex": r"langchain|llamaindex|langgraph", "PyTorch / TensorFlow": r"pytorch|tensorflow",
    "AI coding agents": r"claude code|cursor\b|copilot|windsurf|ai[\s-]assisted (?:development|coding)|coding agents?",
    "LLM evaluation": r"\bevals?\b|evaluation (?:of|for) (?:llm|retrieval|rag)|hit@|\bmrr\b|ndcg",
    # frontend
    "React": r"\breact\b(?!\s*native)", "Vue": r"\bvue(?:\.js)?\b", "Angular": r"\bangular\b",
}
TECH_RX = {name: re.compile(rx, 0 if name == "Go" else _I) for name, rx in TECH.items()}
# "is there any technology here" in one pass instead of ~100 separate searches (Go — case-sensitive)
_TECH_ANY_RX = re.compile("|".join(f"(?:{rx})" if name == "Go" else f"(?i:{rx})" for name, rx in TECH.items()))

# What the CV says in other words
CV_ALIASES = {
    "CI/CD": r"gitlab ci|github actions|jenkins", "Vector search / embeddings": r"chromadb|sentence-transformers|embeddings",
    "LLM": r"\bllms?\b|ollama|rag", "AWS": r"\bs3\b", "AI coding agents": r"claude code|windsurf",
    "LLM evaluation": r"hit@|mrr|retrieval evaluation",
    "asyncio": r"tornado|fastapi|aiohttp|websocket",  # async frameworks = asyncio experience
    "Pydantic": r"fastapi",  # FastAPI models are Pydantic
    "Observability": r"prometheus|grafana", "NoSQL": r"\bredis\b", "Git": r"gitlab|github",
    "ORM": r"sqlalchemy", "HTTP": r"rest(?:ful)? api|websocket",
}

_NICE_RX = re.compile(
    r"nice[\s-]to[\s-]have|bonus|\ba plus\b|is a plus|preferred|desirable|advantage|optional|familiarity|"
    r"exposure to|good to have|would be great|ideally|будет плюсом|желательно|приветствуется", _I)
_ALTERNATIVES_RX = re.compile(r"\bor\b|\be\.g\.|such as|similar|equivalent|like\b|/|или|аналог", _I)
# "any framework except Django", "кроме django", "not Django": a technology after such a word in the same part
# of a sentence is not a requirement. Russian «не» and «без» are left out: «не менее 3 лет Python» ("at least
# 3 years of Python") and «без Python не рассматриваем» ("no Python, no interview") still require Python
_NEGATION_RX = re.compile(r"\b(?:except|excluding|other than|no need for|not(?!\s+only))\b|кроме|за исключением", _I)
_CLAUSE_SPLIT_RX =re.compile(r"\band\b(?!\s*/)|\bas well as\b|\bplus\b|;| и ", re.I)
_SENTENCE_RX = re.compile(r"[^\n.!?;•]+")
_ENGLISH_HIGH_RX = re.compile(r"\b(?:c1|c2)\b[^.\n]{0,25}english|english[^.\n]{0,25}\b(?:c1|c2|native|mother tongue)\b|"
                              r"native[\s-]level english|native english", _I)
_DEGREE_RX = re.compile(r"(?:master'?s|msc|m\.sc|phd|ph\.d)[^.\n]{0,40}(?:required|must)|"
                        r"(?:required|must have)[^.\n]{0,40}(?:master'?s|msc|phd)", _I)
# AI CV — if AI/ML is in the role title or LLM/RAG is really the core of the vacancy (several mentions)
_AI_TITLE_RX = re.compile(r"\bai\b|\bml\b|\bllms?\b|machine learning|genai|agentic", _I)
_AI_TEXT_RX = re.compile(r"\bllms?\b|\brag\b|retrieval[\s-]augmented|embeddings?|vector (?:search|database|store)|"
                         r"langchain|llamaindex|prompt engineering|ai agents?", _I)
# About the candidate — settings.toml [cv]: years of experience (0 — do not compare), English level, master's / PhD
def _cv_setting(key, default):
    from . import geo  # geo reads settings.toml on import

    return geo.S.get("cv", {}).get(key, default)


CV_YEARS = int(_cv_setting("years_experience", 0))
CV_ENGLISH = str(_cv_setting("english_level", "")).upper()
CV_HAS_DEGREE = bool(_cv_setting("has_masters_or_phd", False))
# skills you have only in part (settings.toml [cv.partial]: AWS = "S3 only") — not a gap, but a caveat
PARTIAL = dict(_cv_setting("partial", {}))
# Fit %: the share of covered key skills weighs 85%, nice-to-have — 15%; mismatches and partial
# matches lower the result
FIT_CORE_WEIGHT = 0.85
FIT_MISMATCH_PENALTY = 15
FIT_PARTIAL_PENALTY = 3
FIT_MAX_WEIGHT = 10  # a key skill weighs by the number of mentions in the text, but no more than this
# Terms outside the dictionary (Odoo, ERP, Temporal…): a capitalised word in the middle of a sentence
# that appears in the text UNKNOWN_MIN_MENTIONS times or more, or is in the vacancy title
UNKNOWN_MIN_MENTIONS = 3
UNKNOWN_MAX_WEIGHT = 5  # a term outside the dictionary does not outweigh verified technologies
# a requirement line: a term in it is a skill even if mentioned once
_REQUIREMENT_LINE_RX = re.compile(
    r"experience (?:with|in|using|of)|knowledge of|proficien\w*|familiar\w*|hands[\s-]on|expertise|"
    r"strong (?:skills|background|understanding)|understanding of|worked with|background in|"
    r"опыт (?:работы )?(?:с|в)|знани\w+|владени\w+|умени\w+ работать",
    re.I,
)
# boilerplate lines where capitalised words are not skills: salary, benefits, equal opportunity
_BOILERPLATE_RX = re.compile(
    r"equal opportunit|fair chance|affirmative action|disabilit|veteran|gender identity|national origin|"
    r"pay range|salary range|base (?:pay|salary)|compensation|annually|per (?:year|annum)|\bUSD\b|\bEUR\b|"
    r"benefits|health (?:insurance|care)|401\(?k|paid time off|parental leave|stock options?|equity",
    re.I,
)
# field labels of a vacancy card: "Industry: Sports Trading", "Location: …" — an industry and a place, not skills
_FIELD_LINE_RX = re.compile(
    r"\s*(?:industry|sector|domain|location|salary|employment|schedule|company|отрасль|сфера|локация|"
    r"зарплата|занятость|график|компания)\s*:", re.I)
_MIDSENTENCE_TERM_RX =re.compile(r"(?<=[a-z0-9,;:(/] )([A-Z][A-Za-z0-9+#]{1,30}|[A-Z]{2,}[a-z]?)(?=[\s.,;:)/-]|$)")
_TITLE_TERM_RX = re.compile(r"\b([A-Z][A-Za-z0-9+#]{1,30})\b")
_LINK_TOKEN_RX = re.compile(r"\S\.(?:sh|com|io|ai|org|net|dev|app)|https?://")  # "odoo.sh", "github.com/…", links
# words written with a capital letter that are neither technologies nor skills
_GENERIC_TERMS = set("""
software engineer engineers engineering developer developers development backend frontend fullstack senior junior
middle lead principal staff head team teams group company product products platform platforms services service
solutions solution systems system best place work master masters bachelor degree computer science information
technology technologies technical functional certification certified english russian spanish german europe european
remote office hybrid inc ltd llc gmbh we you our the and data cloud business financial finance global international
applications application enterprise management manager managers operations customer customers partner partners client
clients mobile web api apis ui ux ceo cto cpo coo hr it qa ci cd sre saas b2b b2c faq eu us uk usa emea gmt utc cet
pto ai ml monday tuesday wednesday thursday friday january february march april may june july august september
october november december key core new about who what why how join apply benefits requirements responsibilities
mission vision values equal opportunity employer diversity inclusion health insurance salary bonus equity stock
fintech igaming crypto trading sports sport sportsbook betting gaming casino market markets marketing sales legal compliance security research design
architecture scientist science analyst analytics interview interviews case cases assurance integration
integrations stack officer specialist executive relationship people brand reporting acquisition risk payments
events creative producer designer counsel safety construction balance did north south east west african indian
asian american china india familiarity proven machine learning generative excellent ability
amsterdam america americas nasdaq israel spain london berlin paris cyprus limassol poland warsaw
portugal lisbon netherlands germany france canada brazil mexico singapore dubai uae tbilisi georgia armenia
yerevan kazakhstan almaty ireland dublin switzerland zurich sweden stockholm estonia tallinn lithuania vilnius
google microsoft apple amazon meta facebook venmo paypal openai anthropic nvidia ads netflix uber airbnb stripe
ii iii full power id gtm salesforce codex stem phd msc bsc
""".split())


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


class CvProfile:
    """Skills from the CV and matching against the vacancy text."""

    def __init__(self, settings: dict) -> None:
        """settings — the [cv] section of settings.toml: files = [paths to PDFs], names = [labels]."""
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        cached = len(cache)
        self.cvs: list[tuple[str, str]] = []
        names = settings.get("names") or []
        for i, raw in enumerate(settings.get("files") or []):
            path = Path(raw)
            if not path.is_absolute():  # "cv/My_Backend_CV.pdf" — from the project root (the same in the container)
                path = config.ROOT / path
            label = names[i] if i < len(names) else path.stem
            key = f"{path}|{path.stat().st_mtime if path.exists() else 0}"
            if key not in cache:
                if not path.exists():
                    log.warning("CV %s not found — matching against it is skipped", path)
                    continue
                cache[key] = _pdf_text(path)
            self.cvs.append((label, cache[key]))
        if len(cache) != cached:  # entries are only added: write only when a new PDF was parsed
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        text = "\n".join(t for _, t in self.cvs)
        # the CV generator base (cv/base.toml) is also true about skills, even if they are not in the PDF yet
        base = config.CV_BASE_FILE
        if base.exists():
            text += "\n" + base.read_text(encoding="utf-8")
        self.cv_text = text.lower()
        self.have = {name for name, rx in TECH_RX.items() if rx.search(text)}
        self.have |= {name for name, rx in CV_ALIASES.items() if re.search(rx, text, _I)}
        self._known = {n.lower() for n in TECH_RX} | {n.lower() for n in CV_ALIASES}
        self._term_known: dict[str, bool] = {}  # term -> already known (dictionary / CV); the same terms
        # appear in hundreds of vacancies, and a check against the CV text is a regex search

    def _known_term(self, term: str) -> bool:
        """The term is not needed in "missing from the CV": short, generic, from the technology dictionary or already in the CV."""
        if term not in self._term_known:
            low = term.lower()
            self._term_known[term] = bool(
                len(term) < 2 or low in _GENERIC_TERMS or low in self._known
                or re.search(rf"(?<![a-z0-9]){re.escape(low)}(?![a-z0-9])", self.cv_text)
                or _TECH_ANY_RX.search(term))
        return self._term_known[term]

    def _unknown_terms(self, job, text: str) -> dict[str, int]:
        """Technical terms outside the dictionary that are missing from the CV: {term: how many times in the text}."""
        company = (getattr(job, "company", "") or "").lower()
        # boilerplate lines (salary, benefits, equal opportunity) are not about skills
        lines = [line for line in text.splitlines() if not _BOILERPLATE_RX.search(line) and not _FIELD_LINE_RX.match(line)]
        text = "\n".join(lines)
        mid = Counter(_norm_term(m) for m in _MIDSENTENCE_TERM_RX.findall(text))
        # requirement lines: "Experience with Temporal" — one mention is enough
        required = {_norm_term(m) for line in lines if _REQUIREMENT_LINE_RX.search(line)
                    for m in _MIDSENTENCE_TERM_RX.findall(line)}
        title_terms = {_norm_term(m) for m in _TITLE_TERM_RX.findall(job.title)}
        # for the "is it ever written in lowercase" check — without links and domains ("odoo.sh", "github.com/…")
        # token by token, not one regex: "\S+\.(?:com…)\S*" on a long word without spaces (base64, a 60 KB link)
        # backtracked from every position — O(n²), 9 s per vacancy (found by schemathesis on /api/cover)
        plain = re.sub(r"\S+", lambda m: " " if _LINK_TOKEN_RX.search(m.group()) else m.group(), text)
        found = {}
        for term in set(mid) | title_terms:
            low = term.lower()
            if self._known_term(term) or (company and (low in company or company.startswith(low))):
                continue
            # an ordinary word is written in lowercase at least once ("learning", "range", "pay"), a name never is
            if term != low and re.search(rf"(?<![\w-]){re.escape(low)}(?![\w])", plain):
                continue
            # a field label: "Location: Spain", "Salary: …"
            if re.search(rf"(?<![\w-]){re.escape(term)}\s*:", text):
                continue
            # key if: it is in a requirement line; or it shows up often (a vacancy "about Odoo");
            # or it is in the vacancy title ("Backend Engineer, Odoo") and mentioned in the text
            if term in required or mid[term] >= UNKNOWN_MIN_MENTIONS or (term in title_terms and term in text):
                found[term] = min(UNKNOWN_MAX_WEIGHT, max(1, mid[term]))
        return found

    def analyze(self, job, text: str) -> None:
        """Fills job.cv_have / cv_missing / cv_mismatch / cv_full / cv_pick and Fit % split into
        key skills and nice-to-have (job.cv_fit, cv_core_*, cv_nice_*)."""
        from . import verify

        have, missing = set(), []
        core_have: set[str] = set()
        core_missing: set[str] = set()
        nice_have: set[str] = set()
        nice_missing: set[str] = set()
        for m in _SENTENCE_RX.finditer(f"{job.title}\n{text}"):
            sentence = m.group()
            if not _TECH_ANY_RX.search(sentence):  # most sentences have no technologies
                continue
            hits = [name for name, rx in TECH_RX.items() if rx.search(sentence)]
            if not hits:
                continue
            # parts of a sentence: "frontend (React, JS) and backend, ideally Python (C++ or Java)" — "ideally"
            # and "or" apply only to their own part, React is required here
            cuts = [0, *(m.end() for m in _CLAUSE_SPLIT_RX.finditer(sentence)), len(sentence) + 1]
            clauses = [sentence[a:b] for a, b in zip(cuts, cuts[1:])]
            # "Nice to have: Go, Rust and Kafka" — a caveat at the start of a sentence applies to the whole sentence
            header_nice = bool(_NICE_RX.search(sentence[:40]))

            def clause_of(name, sentence=sentence, cuts=cuts):  # called right here, in this iteration
                m = TECH_RX[name].search(sentence)
                pos = m.start() if m else 0
                idx = next(i for i, (a, b) in enumerate(zip(cuts, cuts[1:])) if a <= pos < b)
                return idx, bool(_NEGATION_RX.search(sentence, cuts[idx], pos))

            by_clause: dict[int, list[str]] = {}
            for name in hits:
                idx, negated = clause_of(name)
                if not negated:
                    by_clause.setdefault(idx, []).append(name)
            for idx, names in by_clause.items():
                clause = clauses[idx]
                nice = header_nice or bool(_NICE_RX.search(clause))
                # "Python, Go or Java" — one of those listed in this part of the sentence is enough
                any_of = bool(_ALTERNATIVES_RX.search(clause)) and any(h in self.have for h in names)
                for name in names:
                    if name in self.have:
                        have.add(name)
                        (nice_have if nice else core_have).add(name)
                    elif any_of:
                        continue
                    else:
                        (nice_missing if nice else core_missing).add(name)
                        if not nice and name not in missing:
                            missing.append(name)
        # a skill required somewhere without reservations is key, even if also mentioned as a plus
        nice_have -= core_have
        nice_missing -= core_missing
        full = f"{job.title}\n{text}"
        weights = {name: len(TECH_RX[name].findall(full)) for name in core_have | core_missing}
        for term, count in self._unknown_terms(job, text).items():
            core_missing.add(term)
            weights[term] = count
            if term not in missing:
                missing.append(term)
        mismatch = []
        years = verify._years(text)
        if CV_YEARS and years and years[0] > CV_YEARS:
            mismatch.append(f"{years[0]}+ years of experience (CV: {CV_YEARS})")
        if not CV_HAS_DEGREE and _DEGREE_RX.search(text):
            mismatch.append("master's / PhD required")
        if getattr(job, "mismatch_lang", ""):
            mismatch.append(f"primary language — {job.mismatch_lang}")
        job.cv_have = sorted(have)
        job.cv_missing = missing[:6]
        job.cv_mismatch = mismatch
        job.cv_partial = [f"{n}: {PARTIAL[n]}" for n in sorted(have) if n in PARTIAL]
        # English below C1 in the CV is not a penalty, but a caveat
        if CV_ENGLISH and CV_ENGLISH < "C1" and _ENGLISH_HIGH_RX.search(text):
            job.cv_partial.append(f"English C1+ requested (CV: {CV_ENGLISH})")
        job.cv_full = bool(have) and not missing and not mismatch
        job.cv_core_have, job.cv_core_missing = sorted(core_have), sorted(core_missing)
        job.cv_nice_have, job.cv_nice_missing = sorted(nice_have), sorted(nice_missing)
        job.cv_fit = fit_percent(core_have, core_missing, nice_have, nice_missing, len(mismatch), len(job.cv_partial),
                                 weights)
        ai = len(self.cvs) > 1 and (_AI_TITLE_RX.search(job.title) or len(_AI_TEXT_RX.findall(text)) >= 3)
        job.cv_pick = self.cvs[0][0] if ai or len(self.cvs) == 1 else (self.cvs[1][0] if self.cvs else "")


def fit_percent(core_have, core_missing, nice_have, nice_missing, mismatches: int, partial: int,
                weights: dict | None = None) -> int:
    """Fit % of the vacancy and the CV; -1 — no technology found in the text.

    A key skill weighs by the number of mentions (1…FIT_MAX_WEIGHT): a vacancy with Odoo in every
    sentence and Python mentioned once matches a CV without Odoo poorly.
    """
    if not (core_have or core_missing or nice_have or nice_missing):
        return -1
    weights = weights or {}

    def w(name):
        return min(FIT_MAX_WEIGHT, max(1, weights.get(name, 1)))

    core_total = sum(w(n) for n in core_have) + sum(w(n) for n in core_missing)
    nice_total = len(nice_have) + len(nice_missing)
    core = sum(w(n) for n in core_have) / core_total if core_total else 1.0
    nice = len(nice_have) / nice_total if nice_total else 1.0
    score = 100 * (FIT_CORE_WEIGHT * core + (1 - FIT_CORE_WEIGHT) * nice)
    score -= FIT_MISMATCH_PENALTY * mismatches + FIT_PARTIAL_PENALTY * partial
    return max(0, min(100, round(score)))


def skills_line(job) -> str:
    """"key: ✔ Python, PostgreSQL ✘ Kafka · nice-to-have: ✔ Docker ✘ Go"."""
    parts = []
    for label, ok, bad in (("key", getattr(job, "cv_core_have", []), getattr(job, "cv_core_missing", [])),
                           ("nice-to-have", getattr(job, "cv_nice_have", []), getattr(job, "cv_nice_missing", []))):
        if ok or bad:
            parts.append(f"{label}: " + " ".join(filter(None, [
                "✔ " + ", ".join(ok) if ok else "", "✘ " + ", ".join(bad) if bad else ""])))
    return " · ".join(parts)


def _norm_term(term: str) -> str:
    return re.sub(r"(?:-based|\.)$", "", term)


def summary(job) -> str:
    """One line for the report."""
    if not getattr(job, "cv_have", None) and not getattr(job, "cv_missing", None):
        return ""
    fit = getattr(job, "cv_fit", -1)
    parts = [f"Fit {fit}%" if fit >= 0 else "", "✔ full match with the CV" if job.cv_full else "",
             skills_line(job)]
    if job.cv_mismatch:
        parts.append("mismatch: " + "; ".join(job.cv_mismatch))
    if job.cv_partial:
        parts.append("partial: " + ", ".join(job.cv_partial))
    if job.cv_pick:
        parts.append(f"CV: {job.cv_pick}")
    return " · ".join(p for p in parts if p)

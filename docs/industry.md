# Company industries and the Companies page

[← README](../README.md)

The industry shows who is hiring in fintech, devtools, e-commerce and so on. It does not change the score or the
order of vacancies: it is a slice for searching, not a filter. Code — `jobhunter/industry.py`, tests —
`tests/test_industry.py`.

## A vacancy's industry

Industries (`INDUSTRIES`): fintech, trading, igaming, crypto, adtech, devtools, security, ecommerce, travel,
edtech, healthtech, gamedev, ai, b2b-saas, bigtech, outsource. If none fits — `other`. A vacancy can have several.

How it is decided (`classify`):

1. **Known companies.** A few well-known ones are built in (`BUILTIN_COMPANIES`: Stripe, GitLab, Shopify…); your
   own go to `[industry_companies]` in `settings.toml` or to the 4th field of `companies.txt` (below). If the
   company is known, its industry is taken and the vacancy text is not read: descriptions often list the
   industries of the clients ("our customers are banks, crypto exchanges…"), which are not the company's own.
2. **Weighted patterns** (`_RULES`): each industry has regexes with a weight. Weight 2 is a strong signal
   (`casino`, `neobank`, `cybersecurity`) — one is enough. Weight 1 is a weak one (`odds`, `payments`) — two
   different ones are needed. A match in the company name or the vacancy title counts double. Each pattern
   counts once, however often it occurs.
3. **Threshold** `THRESHOLD = 2`: an industry counts if it scored that much.
4. **iGaming vs gamedev:** "games" at a casino are iGaming. Gamedev stays next to iGaming only if it scored more.

False positives are avoided with whole words and phrases instead of single words:

| Trap | How it is handled |
|---|---|
| `bet` inside between, alphabet | no bare `bet` in the patterns, only `betting`, `bettors` |
| `game` in gamification | whole words and phrases only |
| `bank` in "data bank", "food bank" | banking patterns are phrases or exclude these |
| health insurance, medical, dental | a list of benefits, not healthtech |
| "travel required" | business trips, not travel |
| `payments` in any online shop | a weak signal (weight 1) |

All patterns of an industry are joined into one regex with named groups: one pass over the text instead of one
per pattern (several times faster on long descriptions).

Industries are computed during a run and saved to the report archive `state/jobs/<date>.json` (`industries`,
plus `remote` and `salary` for the market table). Archives from before that are classified when the page is built.
In the HTML report the industry is shown as a tag next to the company.

## `companies.txt`: industries and brands

```
Company | priority | note | industries | brands
Acme Pay|2|remote-first|fintech|PayRocket, Acme Wallet
```

Both fields are optional. Industries are comma-separated codes from `INDUSTRIES`; they win over the guess from
texts. Brands are product names under which the company posts vacancies: a vacancy whose company field is a brand
counts for the owner, and a post without a company ("NDA", a staffing agency, Telegram) that names a brand of
4+ characters in its text, too. A company listed twice gets the industries and brands of both lines.

## A company's industry

The page sees vacancies, not companies, so a company's industry comes from its vacancies (`companies_overview`):
an industry counts if at least 40 % of the company's vacancies have it (`COMPANY_SHARE`). A known company takes
its industry from the list.

## The Companies page

`/companies` and `GET /api/companies` — companies by industry over all days of the `state/jobs/` archive.

At the top — **the market by industry** (`market_by_industry`, the `market` field of the API). For each industry:
companies and vacancies (without repeats by company and title), the remote share among vacancies with a known
format, the median salary (EUR per month, the middle of the range; at least 3 ranges) and the technologies asked
for most often. A click on a row filters the list below; "all industries" (the default) removes the filter.

The list of companies:

- how many vacancies (one title from different sources counts once), the last day, the application status,
  a link to the board from `ats_mapping.json`; where the industry comes from — the list or the vacancies;
- filters in the address: `?industry=<code>` and `&empty=0` ("with vacancies only") — a filtered page can be
  saved as a link;
- on top — companies with vacancies and no application, then those you applied to, at the bottom — companies from
  `[industry_companies]` and the marked ones in `companies.txt` without vacancies: search them by hand.
  The built-in examples are not listed there.

The summary is cached (`state/web_cache.json`) by the modification times of the archive, `companies.txt`,
`settings.toml`, the applications and the industry rules, and is warmed up in the background after a run.
When the key changes (say, a run wrote new mail), the page shows the previous summary while the new one is computed
in the background.
`ats_mapping.json` is not part of the cache key: a run rewrites it all the time, so boards are added per request.

## How to fix

- **A company got the wrong industry** — put it into `[industry_companies]` or the 4th field of `companies.txt`:
  the list wins over the text.
- **An industry fires on unrelated vacancies** — find the pattern (`scores()` shows the score per industry),
  narrow it or lower the weight to 1. Add a false-positive test to `tests/test_industry.py`.
- **An industry is not found** — add a pattern: weight 2 if the word is unambiguous, otherwise 1.

---
name: new-source
description: Connecting a new vacancy source to job-hunter — a company site, a job board, an aggregator, an ATS board, a career page or a Telegram channel; checking robots.txt and bot protection, choosing the way (ATS + ats_mapping.json, career_pages, manual_jobs, an adapter in sites.py), a fixture and a FakeHttp test, docs/sources.md. Use when asked to "connect a site / board / channel", "add a company", "why is this company not read", or when given a link to vacancies or a career page.
---

# A new vacancy source (job-hunter)

## 1. Is it allowed at all
- First check `docs/sources.md`: the source may already be connected or rejected, with the reason.
- Network — only through `jobhunter/http.py` (`Http.get` / `Http.post`): it reads robots.txt itself and keeps
  1 request/s per host (`config.HOST_INTERVALS` for slower hosts). Do not write your own `requests` / `urllib` calls.
- robots.txt disallows the needed addresses → not connected (only by hand). Cloudflare / captcha / 403 for our
  user agent → not connected, bot protection is never bypassed. LinkedIn — never.
- Vacancies are rendered by a script and the page has no data → a `career_page` will not read them; look for a
  JSON / API that robots.txt allows, otherwise refuse.

## 2. Which way (from cheap to expensive)
1. **ATS board** (Greenhouse, Lever, Ashby, Workable, Teamtailor…) — the company goes to `companies.txt`, the
   board is guessed by itself; a wrong / missing one — edit `ats_mapping.json` as described in "Fixing
   `ats_mapping.json`" in `docs/sources.md` (`"manual": true`).
2. **Career page without an ATS** — `settings.toml`, `[career_pages]`: `Company = "url"` or
   `{ url, location, links }` (`links` — the start of vacancy addresses, if the menu has similar links). The
   company must be in `companies.txt`; in `ats_mapping.json` — `{"platform": null, "manual": true, "note": "…"}`.
3. **A single vacancy** — `[manual_jobs].urls`.
4. **Your own adapter** — a function `(http: HttpClient) -> Iterator[Job]` in `jobhunter/sites.py` (large
   companies — `bigtech.py`), registered in `SOURCES` (`SOURCES.update({...})`). Reuse the helpers: `_get`,
   `_jsonld(page, "JobPosting")`, `_page_text`, `_next_payload`. Open the full text only for vacancies that
   passed the title filter.
5. **Telegram channel** — first look through a month of posts: are there fitting vacancies, what format; the
   decision is the user's.

`settings.toml` and `companies.txt` are personal: suggest the edit or make it if asked, but they never go to
git; an example for others goes to `settings.example.toml` / `companies.example.txt` if the key is new.

## 3. Test (for an adapter and for any new parsing)
- Save the page / response to `tests/fixtures/<source>_list.html`, `<source>_job.html` — **anonymise** it
  (recruiter names, emails, phones removed) and cut it down to 1–3 vacancies.
- A test in `tests/test_sources.py` like `test_findev_reads_jobposting_and_skips_pages_without_it`:
  `FakeHttp({url: fixture_text("…")})`, check the title, company, location, link and that extra pages are not
  opened (`http.calls`). Found a parsing bug — first a test that shows it.
- Regexes over page text — check them on 50–100 KB without spaces (there were O(n²) cases).
- `pytest -q`, `ruff check .`, `mypy` — green.

## 4. Write it down
- `docs/sources.md`: in the right section — what is read, from where, limits (robots, page limits); a refusal —
  with the reason, so nobody checks it again. Function and constant names — from the code (grep).
- Visible changes (a new flag, a setting) — also `docs/configuration.md`. Then `/prep-commit`.

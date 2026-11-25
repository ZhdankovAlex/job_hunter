# Applications: mail and manual events

[← README](../README.md)

## Statuses from mail

`applications.py` reads your recruiter correspondence in Gmail and builds `reports/applications.html`: for every
company — the current status (📨 applied / 🗓️ stages / ❌ rejected / 🎉 offer), how many stages, the first and
last letter dates, days of silence, the history and a link to the last letter.

- If the token has expired or was revoked, `applications.py` opens the Google sign-in window and waits 3 minutes.
  If sign-in fails or Gmail is unavailable, the page is built from the cache, the reason goes to
  `state/mail_sync.json`, and a red ⚠️ warning appears on the page and in the vacancy report; the same happens if
  mail has not been updated for more than a day. In Google Cloud "Testing" mode a token lives 7 days.
- Mail access is **read-only** (`gmail.readonly`): letters are not sent, labelled or deleted. At most 1 request
  per second.
- Letters are parsed by patterns (`jobhunter/mail.py`): the company comes from the ATS sender name (Greenhouse,
  Lever, Ashby, Workable…), the subject ("Thank you for applying to X", "… at X"), job site texts or the corporate
  domain (`jane@acme.com` → Acme). Letter type: offer > rejection > stage / application in the subject > a real
  invitation in the text (a call link, Calendly, "please share your availability") > application confirmation >
  description of stages. Only the letter's own text is parsed: quoted history ("On … wrote:", lines with ">") is
  cut off. Conditional phrases ("If you are not selected…", "if your profile fits, we will schedule an interview")
  and disclaimers count neither as a rejection nor as an invitation.
- Final status and row colour (`applications_report.effective_status`):
  - 🟢 green — stages are going (an invitation by mail, or an interview event in `applications_manual.toml`), offer;
  - 🟡 yellow — waiting: you applied, no answer yet, no more than `timeout_days` (14) days passed;
  - 🟠 orange — you declined (the last event is `declined`; a new application or stage after it makes the process
    live again);
  - 🔴 red — any rejection: a rejection letter, a `rejected` event, or more than `timeout_days` days of silence
    after an application or a stage.
  A new application after a rejection is a new process: its status counts only events after the first new
  application — with stages there it is 🟢, otherwise 🟡; stages before it (for another vacancy) do not count.
- **Cancelled meetings.** A series of stage letters (pick a slot → confirmation → reminder) that ends with a
  cancellation is not a stage in the funnel; in the history such a letter is "cancelled". A "Meeting cancelled" after a
  rejection does not make it look like silence after a stage. Events written by hand in `applications_manual.toml`
  are never dropped from such a series.
- **Days of silence** — only for live processes (waiting, stages going); empty for rejections and offers; 0, not a
  negative number, for events scheduled in the future.
- "While this opportunity may not be the right fit…" in an application confirmation is a caveat, not a rejection;
  typographic apostrophes (won’t, can’t) are normalised before parsing. Letters are split into sentences before
  conditional phrases and disclaimers are cut, so parsing hundreds of letters takes well under a second.
- Times of events in `applications_manual.toml` are in this computer's time zone (summer and winter differ);
  `[mail].utc_offset_hours` sets it explicitly (useful in Docker with `TZ=UTC`).
- Applications not from mail (Telegram, LinkedIn, calls) go to `applications_manual.toml`: company, source and
  events (`applied` / `interview` / `rejected` / `offer` / `declined` / `other`) with a date, a note and an optional
  vacancy `url`. If the company is also in the mail, the events are merged into one row. Events with a `url` also
  dedupe the vacancy report: a vacancy you applied to (not rejected) is hidden; after a rejection it is shown with
  a "🔁" mark.
- Where the company comes from, besides the sender and the subject: a Google Calendar invitation (the organiser's
  corporate domain), the letter text ("…position with Acme", "interest in Acme's … role"), iCIMS / Recruitee senders
  ("Acme @ icims"). A LinkedIn InMail titled "JOB OFFER AT …" is a recruiter writing first, not an offer; an offer
  mentioned in the process description of an application confirmation is not an offer either.
- Letters that look like an application / stage / rejection but have no clear company go to "Company not
  identified" at the bottom. Fix company spelling in `settings.toml` → `[mail.company_aliases]`; for letters where the
  company is nowhere, bind a sender (part of the name or address, also searched in the signature) in
  `[mail.sender_company]`; your own name in senders goes to `[mail].own_names`.
- **Funnel.** At the top of the page, above the mail line: "Applied N → screening → technical → final → offer" — how many companies got at least to
  each stage, % of applications. The stage is the furthest one by the words in letter subjects and notes (tech /
  live coding / take-home / with the team — tech interview; final / CTO / founder — final; other invitations —
  screening; cancelled meetings and "next: …" plans do not count). The "Got to" column shows it per company; links
  open in a new tab.
- Rows go from the freshest last event to older ones, whatever the status. To group by status — click the "Status"
  header or use the filter buttons above the table.
- The mail cache is `state/mail_cache.json`, the token — `state/gmail_token.json` (`state/` is in `.gitignore`).

`--no-applied` (for both `main.py` and `applications.py`) ignores `applications_manual.toml`.

## Gamification: the Progress page

`jobhunter/gamification.py` builds `reports/progress.html` together with the Applications page
(`applications.build_page`); in the web UI it is the Progress item of the left panel. No network and no external
services: only application events, CVs built in `cv_out/` and the vacancies of the latest report
(`state/jobs/<date>.json`). Rewards are only for your own actions; employer decisions (a stage, an offer, a rejection)
are memorable events, without points and without "the goal is an offer". There is no daily quota: it pushes towards
sending applications blindly. Top to bottom: the main block, the next step, recent wins, badges, a collapsed
"Activity over 8 weeks". Applications does not duplicate it: it has the conversion on top instead.

- **Main block** — the rank, XP with a bar to the next rank, the streak and the 💬 message of the day (explanations —
  on hover).
- **XP** over the whole history: an application 10, a tailored application 20 (`cv_out/` has a `<Company>_<role>` CV),
  a ping to a recruiter 5. No XP for stages and rejections. Interview prep (`/prep`) is not saved to disk, so it gives
  no XP either. Ranks (`RANKS`): Scout (0) → Organizer (400) → Active candidate (1200) → Strategist (3000) →
  Search expert (6000).
- 🔥 **Streak** — workdays in a row with a useful action: an application, a stage or a ping to a recruiter (an `other`
  event in `applications_manual.toml`; a recruiter's letter with a `thread` is not our action). Saturday and Sunday
  neither break nor extend it; an empty today does not reset it until the day is over.
- **Next step** (`next_step`) — one challenge, the first rule that fits: an interview within 7 days → prep (`/prep`);
  "waiting for an answer" longer than `PING_AFTER_DAYS` (7) days → follow up with up to three companies; the latest
  report has vacancies with Fit ≥ `LEAD_FIT` (80%) at companies you haven't applied to (compared like the "already
  applied" mark) → look at 3 of them; otherwise — how many applications are left to the weekly goal.
  🎯 **Weekly goal** — `weekly_goal` applications (8 by default), `[gamification]` in `settings.toml`; several letters
  from one company in a day count as one application.
- **Recent wins** (`recent_wins`) — up to 5 events within 30 days by significance: an offer, a final, a technical
  round, screening (one of each kind, the freshest), new badges (up to two), a weekly and a streak record.
- **Badges** (`TRACKS`, `SINGLES`) — one collection, each badge has a rarity: common / rare / legendary (the 1st, 2nd,
  3rd tier of a track). Six tracks: applications with a tailored CV 5 / 20 / 50, companies with screening 3 / 10 / 30,
  with a technical round 1 / 5 / 12, a workday streak 5 / 10 / 20, goal weeks in a row 2 / 3 / 5, rejections
  10 / 40 / 100 (about resilience, not points). One-offs: 🧊 Icebreaker (common — you wrote to a recruiter first and
  they answered), 📈 On target (rare — application → screening conversion of 15%+, with 20+ applications),
  🏁 Finalist (legendary). The main screen shows up to 8: recently earned and the closest to earning; the rest are
  under a collapsed "All badges (N of M)". An earned badge shows its date when it can be computed (stage days are
  approximate: screening — the first stage with a company, technical — the second, final — the last). A badge earned
  since the page was last opened is marked "new": the list of earned badges is kept in the browser's `localStorage`
  (`jobhunter.progress.badges`), the first visit marks nothing. A badge at 80% gets into the message of the day.
- 💬 **Message of the day** — a template for the situation: an interview soon (the stage date or a date in the
  subject / stage note, "25.11", "Nov 25, 2026", 7 days ahead), a fresh stage or rejection (3 days), silence, a
  streak, close to the goal, the record, a badge or a rank. Urgent situations (an interview, a stage, a rejection)
  win; the choice among the fitting ones is deterministic by date. Once in 3 days a short quote from `QUOTES` goes
  under it.
- **Activity over 8 weeks** (collapsed) — a heatmap of actions (Mon–Sun in rows, weeks in columns), ⭐ the number of
  weeks with the goal met and the weekly record.

## One-time Gmail setup

1. https://console.cloud.google.com → create a project (for example `job-hunter`).
2. "APIs & Services" → "Library" → **Gmail API** → Enable.
3. "OAuth consent screen" → type **External** → fill in the name and your email → add the mailbox you use for
   job search to "Test users".
4. "Credentials" → "Create credentials" → **OAuth client ID** → type **Desktop app** → download the JSON and put it
   in the project root as `gmail_client.json` (it is in `.gitignore`).
5. Run `.venv\Scripts\python.exe applications.py` — a Google sign-in window opens: sign in to that mailbox and allow
   reading. The first run reads all letters for a year (≈1 letter per second), later only new ones.

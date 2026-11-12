"""Application statuses from mail: reads Gmail (read-only) and builds reports/applications.html.

Run: .venv\\Scripts\\python.exe applications.py
The first run opens the Google sign-in window in the browser (needs gmail_client.json, see docs/applications.md).
If Gmail is unavailable, the page is built from the letter cache with a warning, and the exit code is 1.
"""
import logging
import sys
from datetime import date

from jobhunter import applications_report, applied, config, mail, progress

STAGES = [
    ("mail", "Gmail: new letters"),
    ("applied", "Applications from applications_manual.toml"),
    ("render", "Applications page"),
]


def main() -> None:
    progress.rotate_errors_log()
    errors = logging.FileHandler(config.ERRORS_LOG, encoding="utf-8")
    errors.setLevel(logging.WARNING)  # errors.log gets only warnings and errors, like in main.py
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[progress.ConsoleHandler(), errors],
    )
    pipeline = progress.Pipeline("applications", STAGES, skippable={"mail"})
    try:
        error = run()
    except BaseException:
        logging.getLogger("applications").exception("Run interrupted")
        pipeline.finish(ok=False)
        raise
    skipped = progress.was_skipped("mail")  # after finish() there is no run anymore
    pipeline.finish()
    if error and not skipped:  # a skip with the button is not an error
        sys.exit(1)


def run() -> str:
    """Mail -> applications -> page; returns the mail error text or ''."""
    # the Google sign-in library logs a link and a one-time auth code at INFO — they do not belong in the log
    logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
    log = logging.getLogger("applications")
    error = ""
    progress.start("mail")
    try:
        messages, address = mail.fetch_messages()
    except Exception as e:  # the report matters more: the rest works on the cache, and the page warns about the problem
        log.exception("mail was not updated")
        error = f"{type(e).__name__}: {e}"[:200]
        messages, address = mail.cached_messages(), "(not updated)"
    if progress.was_skipped("mail") and not error:
        # letters read before the button are already cached; the sync time is not updated — the mail is incomplete
        error = "mail reading skipped by hand"
        log.warning("%s: the remaining letters wait for the next run", error)
    mail.save_sync(error)
    out, apps, unknown = build_page(messages, address)
    log.info("Done: companies %d, letters without a company %d -> %s", len(apps), len(unknown), out)
    return error


def build_page(messages: list[dict] | None = None, address: str = "(from the letter cache)"):
    """reports/applications.html from letters (by default from the cache, without Gmail) and applications from files.

    The web UI calls it after a new event: the page updates in a fraction of a second.
    -> (path, companies, letters without a company).
    """
    if messages is None:
        messages = mail.cached_messages()
    apps, unknown = mail.summarize(messages)
    progress.count(companies=len(apps), unknown_letters=len(unknown))
    progress.start("applied")
    index = None if "--no-applied" in sys.argv else applied.load_applications()
    progress.start("render")
    config.REPORTS_DIR.mkdir(exist_ok=True)
    page = applications_report.render(date.today(), apps, unknown, address, index, mail.stale_warning())
    out = config.REPORTS_DIR / "applications.html"
    out.write_text(page, encoding="utf-8")
    return out, apps, unknown


if __name__ == "__main__":
    main()

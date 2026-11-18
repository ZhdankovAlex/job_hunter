"""A CV for a vacancy from cv/base.toml.

  python cvgen.py <link or part of a company / vacancy name>   — from the last report (state/jobs_latest.json)
  python cvgen.py --text vacancy.txt --company X --title "Senior Backend Engineer"   — from the vacancy text
  python cvgen.py --render cv_out/2026-10-23/X_Senior.md   — rebuild the PDF after editing the .md
  --one-page — the one-page variant (shrinks until it fits)

The result is cv_out/<date>/<company>_<vacancy>.md (a draft to check) and a .pdf next to it.
The same from the web UI: the "📄 CV" button in the report or the CV page.
"""
import argparse
import io
import json
import sys
from pathlib import Path

from jobhunter import config, cvgen

JOBS_FILE = config.ROOT / "state" / "jobs_latest.json"


def find_job(query: str) -> dict:
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8")) if JOBS_FILE.exists() else []
    exact = [j for j in jobs if j["url"] and j["url"].rstrip("/") == query.rstrip("/")]
    if exact:
        return exact[0]
    q = query.lower()
    found = [j for j in jobs if q in f"{j['company']} {j['title']}".lower()]
    if len(found) == 1:
        return found[0]
    if not found:
        sys.exit(f"Vacancy \"{query}\" not found in {JOBS_FILE.name} (the last report). "
                 "Give the vacancy text: --text file --company … --title …")
    print("Several vacancies found — narrow the query or give a link:")
    for j in found[:20]:
        print(f"  {j['company']} — {j['title']} — {j['url']}")
    sys.exit(1)


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", help="link to the vacancy or part of a company / role name")
    p.add_argument("--text", type=Path, help="file with the vacancy text (if it is not in the report)")
    p.add_argument("--company", default="", help="company (with --text)")
    p.add_argument("--title", default="", help="vacancy title (with --text)")
    p.add_argument("--render", type=Path, help="rebuild HTML / PDF from an edited .md")
    p.add_argument("--no-pdf", action="store_true", help="only .md and .html (except when building for a vacancy)")
    p.add_argument("--one-page", action="store_true", help="the one-page variant")
    p.add_argument("--base", choices=cvgen.GENERIC_VARIANTS, help="a base CV without a vacancy: backend or ai")
    p.add_argument("--style", choices=("classic", "harvard"), default=cvgen.STYLE,
                   help="look: classic (like the base CVs) or harvard")
    args = p.parse_args()

    if args.render:
        html_path, pdf_path = cvgen.render(args.render, pdf=not args.no_pdf, style=args.style)
        print(f"Done: {pdf_path or html_path}")
        return
    if args.base:
        person = "_".join(w.capitalize() for w in cvgen.load_base()["name"].split())  # the name from cv/base.toml
        name = f"{person}_Backend_AI_CV" if args.base == "ai" else f"{person}_Backend_CV"
        cvgen.OUT_DIR.mkdir(exist_ok=True)
        md_path = cvgen.OUT_DIR / f"{name}.md"
        md_path.write_text(cvgen.build_markdown(cvgen.load_base(), "", "", "", generic=args.base), encoding="utf-8")
        html_path, pdf_path = cvgen.render(md_path, pdf=not args.no_pdf, style=args.style)
        print(f"Base CV: {md_path}\nPDF: {pdf_path or html_path}")
        return
    if args.text:
        job = {"company": args.company or "Company", "title": args.title or "Vacancy", "url": "",
               "text": args.text.read_text(encoding="utf-8")}
    elif args.query:
        job = find_job(args.query)
    else:
        p.error("a link / query, --text or --render is required")

    # Fit, bullet selection and (for --one-page) shrinking to one page — the same as the web UI button
    result = cvgen.build_cv(job["company"], job["title"], job["text"], job.get("url", ""),
                            one_page=args.one_page, style=args.style)
    print(f"Fit {result['fit']}% · pages {result['pages']} · draft: {result['md']}\nPDF: {result['pdf']}")


if __name__ == "__main__":
    main()

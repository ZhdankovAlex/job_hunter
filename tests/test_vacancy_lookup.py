"""A vacancy by link: the company and the text from JobPosting. Network — FakeHttp."""
import json

import pytest

from jobhunter import vacancy_lookup
from tests.conftest import FakeHttp

TEXT = "<p>" + "We build sportsbook backends in Python. " * 10 + "</p>"


def page(org: str) -> str:
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Python Developer",
               "hiringOrganization": {"@type": "Organization", "name": org}, "description": TEXT}
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'


@pytest.mark.parametrize(("url", "org", "company"), [
    ("https://career.acme.inc/jobs/1", "Career Hub", "Acme"),
    ("https://jobs.acme-games.com/vacancy/7", "Careers", "Acme-games"),
    ("https://careers.acme.io/p/1", "Acme Gaming", "Acme Gaming"),
])
def test_company_is_employer_not_career_site(url, org, company):
    """A career site returns hiringOrganization "Career Hub" — that is the platform; the employer comes from the domain."""
    found = vacancy_lookup.fetch(url, FakeHttp({url: page(org)}))
    assert found["company"] == company

"""Relocation and remote by country (geo.py, countries — settings.example.toml) and parsing salary ranges (salary.py)."""
import pytest

from jobhunter import geo, salary
from jobhunter.models import Job


def job(location, text):
    return Job(company="Acme", title="Python Backend Engineer", location=location, url="https://acme.example/1",
               source="test", description=f"{text} Python backend engineer.")


@pytest.mark.parametrize("location,text,remote,country,kind", [
    ("Madrid, Spain", "We offer relocation support to Spain.", False, "Spain", geo.RELOCATION),
    ("Amsterdam", "Visa sponsorship and relocation package provided.", False, "Netherlands", geo.RELOCATION),
    ("Lisbon, Portugal", "Remote within Portugal only, must be a resident of Portugal.", True, "Portugal",
     geo.RESIDENTS_ONLY),
    ("Nicosia, Cyprus", "Remote from anywhere.", True, "Cyprus", geo.REMOTE_OK),
    ("Nicosia, Cyprus", "Fully remote.", True, "Cyprus", geo.REMOTE_UNCLEAR),
], ids=["spain-relocation", "nl-relocation", "residents-only", "remote-from-anywhere", "remote-unclear"])
def test_classify_target_country(location, text, remote, country, kind):
    j = job(location, text)
    assert geo.classify(j, remote)
    assert (j.reloc_country, j.reloc_type) == (country, kind)


@pytest.mark.parametrize("location,text,remote", [
    ("Bangalore, India", "Relocation assistance available.", False),  # an excluded country
    ("Tbilisi, Georgia", "Office in Tbilisi.", False),  # an office without relocation
    ("Atlanta, Georgia", "Hybrid in Atlanta, GA, USA.", False),  # a state, not a country
], ids=["excluded-country", "office-without-relocation", "us-state-georgia"])
def test_classify_not_taken(location, text, remote):
    assert not geo.classify(job(location, text), remote)


def test_remote_from_anywhere_in_country_is_not_anywhere():
    j = job("Nicosia, Cyprus", "Remote from anywhere in the US.")
    geo.classify(j, True)
    assert j.reloc_type != geo.REMOTE_OK


def test_georgia_country_vs_us_state():
    assert geo.detect_country("Tbilisi, Georgia") == "Georgia"
    assert geo.detect_country("Atlanta, GA") == ""


def test_relocation_quote_skips_negation():
    assert geo.relocation_quote("We do not offer relocation support.") == ""
    assert "relocation" in geo.relocation_quote("Full relocation support to Cyprus is provided.", "Cyprus")


@pytest.mark.parametrize("text,expected", [
    ("€4,500 - €6,000 per month", (4500, 6000)),
    ("4.5–5.5k EUR", (4500, 5500)),  # "k" only at the second number
    ("€90 - €110K per year", (7500, 9167)),
    ("60 000 – 80 000 EUR gross per year", (5000, 6667)),
    ("Salary: 5000 EUR", (5000, 5000)),
    ("competitive salary", None),
    ("€40 per hour", None),
], ids=["monthly-range", "k-on-second", "k-on-second-yearly", "yearly-spaces", "single", "no-numbers", "hourly"])
def test_salary_parse(text, expected):
    assert salary.parse(text) == expected


def test_k_is_not_carried_over_non_range_numbers():
    """"3 days in office, 60k EUR" — the three does not become 3000."""
    assert salary.parse("3 days in office, salary 60k EUR per year") == (5000, 5000)


@pytest.mark.parametrize("title,text,grade", [
    ("Senior Python Developer", "", "senior"),
    ("Middle Backend Engineer", "", "middle"),
    ("Python Developer", "We need 5+ years of commercial experience", "senior"),
    ("Python Developer", "1 year of experience", "junior"),
    ("Python Developer", "", ""),
])
def test_grade(title, text, grade):
    assert salary.grade(title, text) == grade

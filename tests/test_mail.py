"""Parsing letters: letter kind (applied / stage / rejection / offer) and company.

The letters are made up, but repeat the wording the classification already got wrong.
"""
import time

import pytest

from jobhunter import mail

CASES = [
    # (subject, text, expected kind, test id); the "-ru" cases are letters from Russian-language job sites
    ("Thank you for applying to Acme",
     "We’ve received your application. If your profile matches, we will schedule an interview.",
     mail.APPLIED, "conditional-invite-is-not-invite"),
    ("Thank you for applying for Acme",
     "We’ve received your application. If you don’t hear back from us, it means we’ve moved forward with other "
     "candidates. While this opportunity may not be the right fit, keep an eye on our careers page.",
     mail.APPLIED, "may-not-be-right-fit-is-not-rejection"),
    ("Application Received",
     "Thanks for applying! Unfortunately, this means we can’t respond to you all individually.",
     mail.APPLIED, "cant-respond-disclaimer-curly-apostrophe"),
    ("Acme - Software Engineer Application Update",
     "Thanks so much for your interest. After careful consideration, we won’t be progressing with your application.",
     mail.REJECTED, "rejection-curly-apostrophe"),
    ("Update on your application",
     "Unfortunately, we have decided to move forward with other candidates.",
     mail.REJECTED, "rejection"),
    ("Next steps",
     "We would like to invite you to a call. Please book a time: https://calendly.com/acme/30min",
     mail.INTERVIEW, "calendly-invite"),
    ("Offer letter", "We are pleased to extend an offer to you.", mail.OFFER, "offer"),
    ("Ваш отклик", "К сожалению, мы не готовы пригласить вас на следующий этап.", mail.REJECTED, "rejection-ru"),
    ("Собеседование", "Приглашаем на собеседование, укажите удобное время.", mail.INTERVIEW, "invite-ru"),
]


@pytest.mark.parametrize("subject,body,kind,why", CASES, ids=[c[3] for c in CASES])
def test_classify(subject, body, kind, why):
    assert mail.classify(subject, body) == kind


def test_quoted_reply_is_ignored():
    """The quote of an old letter had a rejection, while the letter itself is a call invitation."""
    body = ("Hi Jane, let's schedule a call — please book a time: https://calendly.com/acme/30min\n\n"
            "On Mon, 3 Nov 2026 at 10:00 Acme <jobs@acme.com> wrote:\n> Unfortunately, the position has been filled.")
    assert mail.classify("Re: your application", body) == mail.INTERVIEW


def test_drop_sentences_keeps_sentence_boundaries():
    text = "We got it. If you fit, we call! Thanks?"
    assert mail._drop_sentences(text, mail._CONDITIONAL_RX) == "We got it. ! Thanks?"


def test_classify_long_letter_is_fast():
    """Regexes like "[^.]*…[^.]*" ran in quadratic time: 440 letters took 13 s to parse."""
    body = ("word " * 40_000) + "Unfortunately, we will not be moving forward."
    start = time.perf_counter()
    assert mail.classify("Update", body) == mail.REJECTED
    assert time.perf_counter() - start < 5  # the quadratic version took 13 s; headroom for --cov


def test_company_from_manual_sender_mapping(monkeypatch):
    """[mail.sender_company]: Acme recruiters write from another domain — the company comes from the setting."""
    monkeypatch.setitem(mail.SETTINGS, "sender_company", {"@hr-agency.example": "Acme"})
    assert mail.company_of("Jane <jane@hr-agency.example>", "Invitation: HR Interview", "") == "Acme"


def test_company_from_subject():
    assert mail.company_of("Hiring Team <no-reply@ashbyhq.com>", "Thank you for applying to Acme", "") == "Acme"


def test_event_date_before_1970_does_not_break_page(tmp_path, monkeypatch):
    """A typo date (0001-01-01) used to break the Applications page on every build: on Windows
    datetime.astimezone() raises OSError before 1970, on Linux it shifts 1 January of year 1 into year 0
    (ValueError)."""
    from jobhunter import config

    f = tmp_path / "applications_manual.toml"
    f.write_text('[[application]]\ncompany = "Acme"\nevents = [{date = 0001-01-01T00:00:00, kind = "applied"}]\n',
                 encoding="utf-8")
    monkeypatch.setattr(config, "MANUAL_APPLICATIONS_FILE", f)
    monkeypatch.setattr(mail, "_LOCAL_TZ", None)
    [(company, event)] = mail.manual_events()
    assert company == "Acme"
    assert event.when.year == 1
    assert event.when.tzinfo is not None

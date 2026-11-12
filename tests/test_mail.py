"""Разбор писем: тип письма (отклик / этап / отказ / оффер) и компания.

Письма выдуманные, но повторяют формулировки, на которых классификация уже ошибалась.
"""
import time

import pytest

from jobhunter import mail

CASES = [
    # (тема, текст, ожидаемый тип, id теста)
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
    """В цитате старого письма был отказ, а само письмо — приглашение на звонок."""
    body = ("Hi Jane, let's schedule a call — please book a time: https://calendly.com/acme/30min\n\n"
            "On Mon, 3 Nov 2026 at 10:00 Acme <jobs@acme.com> wrote:\n> Unfortunately, the position has been filled.")
    assert mail.classify("Re: your application", body) == mail.INTERVIEW


def test_drop_sentences_keeps_sentence_boundaries():
    text = "We got it. If you fit, we call! Thanks?"
    assert mail._drop_sentences(text, mail._CONDITIONAL_RX) == "We got it. ! Thanks?"


def test_classify_long_letter_is_fast():
    """Регулярки «[^.]*…[^.]*» работали за квадратичное время: 440 писем разбирались 13 с."""
    body = ("word " * 40_000) + "Unfortunately, we will not be moving forward."
    start = time.perf_counter()
    assert mail.classify("Update", body) == mail.REJECTED
    assert time.perf_counter() - start < 1


def test_company_from_manual_sender_mapping(monkeypatch):
    """[mail.sender_company]: рекрутеры Acme пишут с чужого домена — компания из настройки."""
    monkeypatch.setitem(mail.SETTINGS, "sender_company", {"@hr-agency.example": "Acme"})
    assert mail.company_of("Jane <jane@hr-agency.example>", "Invitation: HR Interview", "") == "Acme"


def test_company_from_subject():
    assert mail.company_of("Hiring Team <no-reply@ashbyhq.com>", "Thank you for applying to Acme", "") == "Acme"

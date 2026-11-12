"""Общая настройка тестов.

Модули jobhunter читают settings.toml при импорте. Чтобы тесты давали одно и то же у всех и в CI,
до любого импорта подставляются файлы-примеры из репозитория (личные настройки не нужны). Настройки —
settings.example.toml с тестовыми значениями (домашняя страна, страны релокации), см. _test_settings().
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"



def _test_settings() -> Path:
    """settings.example.toml + значения, на которые опираются тесты: домашняя страна (вымышленный пример —
    Польша), страны релокации, исключённая страна, «только из России», опыт в резюме."""
    text = (ROOT / "settings.example.toml").read_text(encoding="utf-8")
    for old, new in [
        ('country = ""', 'country = "Poland"'),
        ('iso = ""', 'iso = "PL"'),
        ('places = []', 'places = ["Warsaw", "Krakow", "Польша", "Варшава"]'),
        ('relocation_countries = ["Netherlands", "Germany", "Spain"]',
         'relocation_countries = ["Spain", "Netherlands", "Portugal", "Cyprus", "Georgia", "Germany"]'),
        ("exclude_countries = []", 'exclude_countries = ["India"]'),
        ("[places]\n", '[places]\nIndia = ["Bengaluru", "Bangalore", "Hyderabad", "Pune"]\n'
                       'Portugal = ["Lisbon", "Lisboa", "Porto"]\nCyprus = ["Limassol", "Nicosia", "Larnaca"]\n'
                       'Georgia = ["Tbilisi", "Batumi", "Kutaisi"]\n'),
        ("hide_russia_only = false", "hide_russia_only = true"),
        ("years_experience = 0", "years_experience = 6"),
    ]:
        assert old in text, f"settings.example.toml изменился: нет «{old}»"
        text = text.replace(old, new, 1)
    path = Path(tempfile.mkdtemp(prefix="jobhunter-tests-")) / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


os.environ["JOBHUNTER_SETTINGS"] = str(_test_settings())
os.environ["JOBHUNTER_PROFILE"] = str(ROOT / "profile.example.toml")
os.environ["JOBHUNTER_CV_BASE"] = str(ROOT / "cv" / "base.example.toml")
os.environ["JOBHUNTER_NO_WARM"] = "1"  # web.app не считает кэш в фоне на настоящих данных


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = ""):
        self.text, self.status_code, self.url = text, status_code, url

    def json(self):
        import json

        return json.loads(self.text)


class FakeHttp:
    """Вместо jobhunter.http.Http: адрес -> текст ответа; запросы записываются в .calls. Сети нет."""

    def __init__(self, pages: dict[str, str]):
        self.pages, self.calls = pages, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.pages:
            return FakeResponse("", 404, url)
        return FakeResponse(self.pages[url], 200, url)


@pytest.fixture
def now():
    return datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def at(now):
    """at(-3) — три дня назад от «сейчас» теста."""
    return lambda days, hours=0: now + timedelta(days=days, hours=hours)

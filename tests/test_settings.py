"""The settings.toml model: the example is valid, a typo in a key and a wrong type are errors with the key path, --check."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jobhunter import config, settings

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "settings.example.toml"


def test_example_is_valid() -> None:
    s = settings.load(EXAMPLE)
    assert s.max_age_days == 60
    assert s.freehire.queries[0].name
    assert s.telegram.channels and s.cv.names


def test_defaults_match_code() -> None:
    """An empty file gives the defaults the code had before (geo.py, config.py and others)."""
    s = settings.Settings()
    assert s.relocation_countries == [] and s.any_country is False
    assert s.relocation_enabled is True and s.country_bound_remote is True
    assert s.max_age_days == 30 and s.top_limit == 100 and s.top_per_company == 3
    assert s.freehire.enabled is True and s.freehire.max_experience_years == 7
    assert s.mail.timeout_days == 14 and s.mail.utc_offset_hours is None and s.gamification.weekly_goal == 8
    assert s.cv.auto_fit_min == 80 and s.cv.auto_max == 40 and s.cv.style == "classic"
    assert s.bigtech.queries == ["python", "backend"]
    assert s.home.country == "" and s.telegram.enabled is False and s.telegram.max_age_days == 60


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "settings.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_unknown_key_is_error_with_path(tmp_path: Path) -> None:
    p = _write(tmp_path, "[freehire]\nmax_reqests = 5\n")
    with pytest.raises(settings.SettingsError) as e:
        settings.load(p)
    assert "freehire.max_reqests" in str(e.value)


def test_wrong_type_is_error_with_path(tmp_path: Path) -> None:
    p = _write(tmp_path, 'max_age_days = "sixty"\n[salary.rates_to_eur]\nUSD = "x"\n')
    with pytest.raises(settings.SettingsError) as e:
        settings.load(p)
    assert "max_age_days" in str(e.value)
    assert "salary.rates_to_eur.USD" in str(e.value)


def test_broken_toml_is_error(tmp_path: Path) -> None:
    with pytest.raises(settings.SettingsError, match="TOML"):
        settings.load(_write(tmp_path, "max_age_days = \n"))


def test_freehire_query_allows_api_params(tmp_path: Path) -> None:
    """freehire query parameters are API names, the API checks them itself (meta.ignored_params)."""
    p = _write(tmp_path, '[[freehire.queries]]\nname = "X"\ncountries = "PT"\nopen_within_days = 30\n')
    q = settings.load(p).freehire.queries[0]
    assert q.name == "X" and q.params() == {"countries": "PT", "open_within_days": 30}


def test_use_changes_modules_without_reload() -> None:
    """Modules read the settings when called: settings.use() changes their behaviour without a reload."""
    from jobhunter import applications_report, bigtech, cvgen, geo, sites

    settings.use(settings.Settings())
    assert geo.detect_country("Lisbon, Portugal") == ""
    assert "workday:Acme" not in sites.sources()
    settings.use(settings.Settings(
        relocation_countries=["Portugal"], places={"Portugal": ["Lisbon"]},
        referral_companies=["Acme"],
        bigtech=settings.Bigtech(workday=[["Acme", "acme", "acme", "jobs"]]),
        mail=settings.Mail(timeout_days=3), cv=settings.Cv(style="harvard")))
    assert geo.detect_country("Lisbon, Portugal") == "Portugal"
    assert geo.rules().referral_companies == {"acme"}
    assert "workday:Acme" in bigtech.sources() and "workday:Acme" in sites.sources()
    assert applications_report.timeout_days() == 3
    assert cvgen.default_style() == "harvard"


def test_get_is_lazy_and_overridable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "_current", None)
    monkeypatch.setattr(config, "SETTINGS_FILE", _write(tmp_path, "top_limit = 7\n"))
    assert settings.get().top_limit == 7
    assert settings.get() is settings.get()  # the file is read once
    settings.use(settings.Settings(top_limit=3))
    assert settings.get().top_limit == 3


def test_check_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert settings.main(["--check", str(EXAMPLE)]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_bad(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(tmp_path, "top_limt = 5\n[mail]\ntimeout_days = [1]\n")
    assert settings.main(["--check", str(p)]) == 1
    out = capsys.readouterr().out
    assert "top_limt" in out and "mail.timeout_days" in out


def test_check_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert settings.main(["--check", str(tmp_path / "nope.toml")]) == 1
    assert "nope.toml" in capsys.readouterr().out


def test_manual_job_entry_can_be_table(tmp_path: Path) -> None:
    p = _write(tmp_path, '[manual_jobs]\nurls = ["https://example.com/a", { url = "https://example.com/b", '
                         'company = "Acme" }]\n')
    urls = settings.load(p).manual_jobs.urls
    assert urls[0] == "https://example.com/a"
    assert isinstance(urls[1], settings.ManualJob) and urls[1].company == "Acme"


def test_settings_module_imports_with_broken_file(tmp_path: Path) -> None:
    """jobhunter.settings itself does not read the file on import: --check reports a broken file instead of a
    traceback. (config reads [home] through settings.get() on import, so `--check` must not import it first.)"""
    bad = _write(tmp_path, "this is not toml")
    env = {**os.environ, "JOBHUNTER_SETTINGS": str(bad)}
    r = subprocess.run([sys.executable, "-m", "jobhunter.settings", "--check", str(bad)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "TOML" in r.stdout, r.stderr[-2000:]

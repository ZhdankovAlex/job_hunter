"""Какие источники не дают вакансий в отчёт и почему — для страницы «Источники».

Причина у каждой компании одна, по старшинству (`REASONS`): ошибка запроса → проверить руками (VERIFY
в companies.txt) → своя страница без ATS → борд не найден → борд пустой → вакансий нет → вакансии есть,
но ни одна не прошла фильтры за последние `MATCH_DAYS` отчётов. Данные — итог последнего прогона
(`state/sources_latest.json`), `ats_mapping.json`, `companies.txt` и архив отчётов `state/jobs/`.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import config
from .companies import load_companies
from .keys import company_key

SOURCES_FILE = config.ROOT / "state" / "sources_latest.json"
JOBS_DIR = config.ROOT / "state" / "jobs"
MATCH_DAYS = 14  # сколько последних отчётов смотреть для «подходящих нет»

# ключ → (название группы, пояснение для подсказки)
REASONS = {
    "error": ("Ошибка запроса", "Источник ответил ошибкой в последнем прогоне"),
    "verify": ("Проверить руками", "В companies.txt помечено VERIFY: сайт ищется вручную"),
    "manual": ("Своя страница без ATS", "Помечено вручную в ats_mapping.json: читать руками или через [career_pages]"),
    "no_board": ("Борд не найден", "Имя борда не подошло ни к одной ATS; повторная попытка — через "
                                   f"{config.RECHECK_NOT_FOUND_DAYS} дней после проверки"),
    "empty_board": ("Борд пустой", "Борд на ATS нашёлся, но открытых вакансий на нём нет"),
    "zero": ("Вакансий нет", "Источник читается, но в последнем прогоне вакансий 0"),
    "no_match": ("Подходящих нет", f"Вакансии есть, но ни одна не попала в отчёты за последние {MATCH_DAYS} дней"),
}
COMPANY_KINDS = ("ats", "career_page")  # агрегаторы и доски — не компании
_CHECKED = ("verify", "manual", "no_board", "empty_board")  # где дата проверки борда что-то значит


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def matched_companies(jobs_dir: Path = JOBS_DIR, days: int = MATCH_DAYS) -> tuple[set[str], int]:
    """Компании (company_key), у которых хоть одна вакансия попала в отчёт за `days` дней; и число отчётов."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    keys, reports = set(), 0
    for path in sorted(jobs_dir.glob("*.json")):
        if path.stem < cutoff:
            continue
        reports += 1
        keys.update(company_key(j.get("company") or "") for j in _read_json(path, []))
    return keys, reports


def _verify_notes() -> dict[str, str]:
    return {company_key(c.name): c.note.removeprefix("VERIFY").lstrip(": ").strip()
            for c in load_companies() if c.note.startswith("VERIFY")}


def _reason(entry: dict, verify_note: str | None, matched: bool) -> tuple[str, str] | None:
    """(причина, подробности) или None, если компания читается и даёт подходящие вакансии."""
    if entry.get("error"):
        return "error", entry["error"]
    if verify_note is not None:
        return "verify", verify_note
    platform = entry.get("platform")
    if entry["kind"] == "ats" and not platform:
        if entry.get("manual"):
            return "manual", entry.get("note") or entry.get("from") or ""
        if entry.get("empty_board"):
            return "empty_board", ""  # сам борд виден в колонке «ATS / борд»
        checked = entry.get("checked_at") or ""
        try:
            retry = date.fromisoformat(checked) + timedelta(days=config.RECHECK_NOT_FOUND_DAYS)
            return "no_board", f"следующая попытка — {retry.isoformat()}"
        except ValueError:
            return "no_board", ""
    if entry["jobs"] == 0:
        return "zero", ""  # борд и число вакансий видны в соседних колонках
    if entry["kind"] in COMPANY_KINDS and not matched:  # агрегатор или доска — не компания, в отчёте их не ищем
        return "no_match", ""
    return None


def gaps(sources: dict | None = None, mapping: dict | None = None, matched: set[str] | None = None,
         verify: dict[str, str] | None = None) -> dict:
    """Источники без вакансий в отчёте: {groups: [{key, title, hint, count}], rows: [...], reports: N}.

    Компании (ATS, карьерные страницы) проходят все проверки, агрегаторы и доски — только ошибку и ноль.

    Аргументы — для тестов; по умолчанию всё читается из файлов.
    """
    sources = _read_json(SOURCES_FILE, {}) if sources is None else sources
    mapping = _read_json(config.MAPPING_FILE, {}) if mapping is None else mapping
    reports = None
    if matched is None:
        matched, reports = matched_companies()
    verify = _verify_notes() if verify is None else verify

    rows = []
    for name, entry in sources.items():
        company = entry.get("kind") in COMPANY_KINDS
        if company:
            entry = {**(mapping.get(name) or {}), **entry}  # у career_page поля маппинга не дописаны
        key = company_key(name)
        found = _reason(entry, verify.get(key) if company else None, key in matched)
        if found:
            reason, detail = found
            rows.append({"name": name, "reason": reason, "detail": detail,
                         "checked": entry.get("checked_at", "") if reason in _CHECKED else ""})
    order = list(REASONS)
    rows.sort(key=lambda r: (order.index(r["reason"]), r["name"].lower()))
    counts = {k: sum(r["reason"] == k for r in rows) for k in REASONS}
    groups = [{"key": k, "title": t, "hint": h, "count": counts[k]} for k, (t, h) in REASONS.items() if counts[k]]
    return {"groups": groups, "rows": rows, "reports": reports}

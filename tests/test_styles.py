"""One style base: web/static/tokens.css — colors, fonts, the size scale, radii.

The UI (app.css) and the HTML pages in reports/ (the report, "Applications") share one palette, and a font size
is only a step of the scale — not a number picked by eye.
"""
import re
from datetime import date

import pytest

from jobhunter import applications_report, config, html_report

STATIC = config.ROOT / "web" / "static"
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
SOURCES = {
    "app.css": (STATIC / "app.css").read_text(encoding="utf-8"),
    "html_report._CSS": html_report._CSS.removeprefix(html_report.TOKENS_CSS),
    "applications_report._CSS_COLORS": applications_report._CSS_COLORS,
}
DEFINED_RX = re.compile(r"(--[\w-]+)\s*:")
USED_RX = re.compile(r"var\((--[\w-]+)\s*\)")  # with a fallback (var(--n, 3)) — set from a template / JS


@pytest.mark.parametrize("name", SOURCES)
def test_font_sizes_only_from_scale(name):
    raw = re.findall(r"font-size:\s*([^;}\n]+)", SOURCES[name])
    assert raw, name
    assert [v for v in raw if not v.strip().startswith("var(--fs-")] == [], f"{name}: font size not from the --fs-* scale"
    assert not re.search(r"font:\s*\d", SOURCES[name]), f"{name}: font with a numeric size"


@pytest.mark.parametrize("name", SOURCES)
def test_palette_defined_only_in_tokens(name):
    own = set(DEFINED_RX.findall(SOURCES[name]))
    palette = set(DEFINED_RX.findall(TOKENS))
    assert not own & palette, f"{name} redefines tokens.css variables: {sorted(own & palette)}"


@pytest.mark.parametrize("name", SOURCES)
def test_every_variable_is_defined(name):
    defined = set(DEFINED_RX.findall(TOKENS)) | set(DEFINED_RX.findall(SOURCES[name]))
    missing = sorted(set(USED_RX.findall(SOURCES[name])) - defined)
    assert missing == [], f"{name}: no such variables in tokens.css: {missing}"


def test_report_pages_embed_tokens():
    """Pages in reports/ also open without the server — the style base is embedded in full."""
    assert html_report._CSS.startswith(TOKENS)
    page = applications_report.render(date(2026, 1, 5), [], [], "me@example.com")
    assert "--fs-md:" in page and "--accent:" in page


def test_interface_loads_tokens_before_app_css():
    base = (config.ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("/static/tokens.css") < base.index("/static/app.css")


TEMPLATES_DIR = config.ROOT / "web" / "templates"
TEMPLATES = sorted(p for p in TEMPLATES_DIR.glob("*.html") if not p.name.startswith("_"))
# pages with their own layout: "Pipeline" — the run card instead of a header; reports and "Applications" —
# a ready page from reports/ in a frame (it has its own h1 and .sub with the same spacing)
OWN_LAYOUT = {"base.html", "pipeline.html", "reports.html", "applications.html"}


@pytest.mark.parametrize("path", [p for p in TEMPLATES if p.name not in OWN_LAYOUT], ids=lambda p: p.name)
def test_page_starts_with_standard_head(path):
    """The page header is the ui.page_head macro: title, lead and the spacing to the first block are the same everywhere.
    The rendered markup is checked by tests/test_web.py::test_page_head_rendered."""
    text = path.read_text(encoding="utf-8")
    assert '{% import "_ui.html" as ui %}' in text
    content = re.sub(r"\{% set \w+ %\}.*?\{% endset %\}", "", text.split("{% block content %}", 1)[1], flags=re.S).lstrip()
    assert content.startswith(("{{ ui.page_head(", "{% call ui.page_head(")), f"{path.name}: the page does not start with ui.page_head"


def test_text_on_accent_from_token():
    """Text on an --accent fill — only var(--on-accent): in the dark theme the accent is light and white text is unreadable."""
    for name, css in SOURCES.items():
        assert not re.search(r"color:\s*#fff(?![0-9a-fA-F])", css), f"{name}: color: #fff — use var(--on-accent)"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_style_only_through_classes(path):
    """No one-off styles in templates: style= only carries data — a bar width or a column count (--n)."""
    for style in re.findall(r'style="([^"]*)"', path.read_text(encoding="utf-8")):
        props = [d.split(":")[0].strip() for d in style.split(";") if d.strip()]
        assert all(p in ("width", "--n") for p in props), f'{path.name}: style="{style}" — needs a class in app.css'


def test_controls_share_one_height():
    """Buttons, inputs and selects share the height --ctl-h; smaller — only .sm and toggle filters (--ctl-h-sm)."""
    app = SOURCES["app.css"]
    assert re.search(r"button, \.btn \{[^}]*height: var\(--ctl-h\)", app)
    assert re.search(r":where\(input:not\(\[type=checkbox\][^{]*select, textarea\) \{[^}]*height: var\(--ctl-h\)", app)
    # fields are styled in one place: no "font: inherit; … border" on individual forms
    assert len(re.findall(r"font: inherit", app)) == 2, "font: inherit — only on the base buttons and fields"


def test_page_rhythm_from_spacing_scale():
    app = SOURCES["app.css"]
    assert all(f"--sp-{i}:" in TOKENS for i in range(1, 7))
    assert re.search(r"\.page \{[^}]*gap: var\(--sp-4\)", app)
    assert re.search(r"\.page > \* \{ margin-top: 0; margin-bottom: 0;", app)  # block spacing comes only from gap
    assert app.rstrip().splitlines()[-1].startswith(".page > *")  # at the end of the file — overrides single-class rules


def _block(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_theme_palette_by_attribute_and_system():
    """The theme toggle: data-theme="dark" — the dark palette, "light" overrides the system dark one; no attribute —
    prefers-color-scheme. The dark palette is written twice, the blocks must match."""
    chosen = _block(TOKENS, ':root[data-theme="dark"]')
    system = _block(TOKENS.split("@media (prefers-color-scheme: dark)", 1)[1], ':root:not([data-theme="light"])')
    assert "color-scheme: dark" in chosen
    assert sorted(DEFINED_RX.findall(chosen)) == sorted(DEFINED_RX.findall(system))
    assert chosen.replace("color-scheme: dark;", "").split() == system.split()
    assert "color-scheme: light" in _block(TOKENS, ':root[data-theme="light"]')


def test_interface_applies_theme_before_render():
    base = (config.ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    head = base[:base.index("</head>")]
    assert "localStorage.getItem('theme')" in head and "dataset.theme" in head
    assert 'id="theme-toggle"' in base


def test_report_pages_follow_interface_theme():
    """Pages in reports/ inside a frame read the theme from the same localStorage before rendering; from disk (file:) — the system."""
    js = html_report.THEME_JS
    assert "localStorage.getItem('theme')" in js and "'file:'" in js and "'storage'" in js
    day = date(2026, 1, 5)
    pages = {
        "html_report": html_report.render(day, [], {}),
        "applications_report": applications_report.render(day, [], [], "me@example.com"),
    }
    for name, page in pages.items():
        head = page[:page.index("</head>")]
        assert js in head and head.index(js) < head.index("<style>"), name

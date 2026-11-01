"""E15-S5: the web UI's own wording and colours (PLAN.md §9.14, D-029). Our templates use no
verdict or advice words (data from filings, such as a company named Target, isn't ours to
reword), and every text colour keeps a contrast of at least 4.5:1 with its background in the
light and the dark theme."""

import re
from pathlib import Path

import pytest

from marketdata.web.assets import STATIC_DIR

TEMPLATES = Path(STATIC_DIR).parent / "templates"
VERDICTS = re.compile(
    r"\b(buy|buys|sell|sells|hold|holds|strong|weak|best|worst|outperform\w*|underperform\w*"
    r"|bullish|bearish|recommend\w*|ratings?|scores?|signals?|targets?|top movers?|should"
    r"|undervalued|overvalued|cheap|expensive|winners?|losers?|beat|beats|missed)\b",
    re.IGNORECASE,
)
# Jinja code and comments, then markup: what is left is the text a reader sees.
CODE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
MARKUP = re.compile(r"<[^>]+>")


def _words(path: Path) -> str:
    return MARKUP.sub(" ", CODE.sub(" ", path.read_text()))


@pytest.mark.parametrize("template", sorted(TEMPLATES.rglob("*.html")), ids=lambda path: path.name)
def test_no_template_uses_verdict_or_advice_words(template: Path) -> None:
    assert VERDICTS.findall(_words(template)) == []


def test_the_check_would_catch_one() -> None:
    assert VERDICTS.findall("A strong quarter: you should buy.") == ["strong", "should", "buy"]
    assert VERDICTS.findall("held by curated managers; holdings; targeted") == []


def _palette(css: str, block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z]+):\s*(#[0-9a-f]{6})", block))


def _themes() -> dict[str, dict[str, str]]:
    css = (STATIC_DIR / "app.css").read_text()
    light = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL)
    dark = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", css, re.DOTALL)
    assert light is not None and dark is not None
    return {"light": _palette(css, light.group(1)), "dark": _palette(css, dark.group(1))}


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Text colour on background, as the stylesheet uses them; the button's text is the page colour.
PAIRS = [
    (text, background)
    for background in ("bg", "chip", "head")
    for text in ("fg", "muted", "accent", "ok", "warn", "bad")
] + [("bg", "accent")]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_keeps_a_contrast_of_4_5_to_1(theme: str) -> None:
    colours = _themes()[theme]
    low = {
        f"{text} on {background}": round(_contrast(colours[text], colours[background]), 2)
        for text, background in PAIRS
        if _contrast(colours[text], colours[background]) < 4.5
    }
    assert low == {}

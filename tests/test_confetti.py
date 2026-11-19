"""Tests for src/ui/confetti.py: the confetti HTML (which wins get it: test_win_announcements.py)."""

import re
from src.ui import confetti


# --- the HTML -------------------------------------------------------------------

def test_markup_is_one_line_without_scripts():
    html = confetti.markup()
    assert "\n" not in html
    assert "<script" not in html.lower() and "onload" not in html.lower()


def test_markup_has_every_piece_and_never_blocks_clicks():
    html = confetti.markup()
    assert html.count('class="ci-cf"') == confetti.PIECES
    assert "pointer-events:none" in html
    assert 'aria-hidden="true"' in html


def test_page_animation_is_paused_so_the_confetti_covers_the_screen():
    """A transform animation on the page box would trap the fixed layer inside the page."""
    assert '[class*="st-key-page_"]:has(.ci-confetti){animation:none!important;' in confetti.CSS


def test_markup_respects_reduced_motion():
    assert "@media (prefers-reduced-motion:reduce){.ci-confetti{display:none}}" in confetti.markup()


def test_markup_uses_only_ci_prefixed_classes():
    classes = re.findall(r'class="([^"]+)"', confetti.markup())
    assert classes and all(name.startswith("ci-") for c in classes for name in c.split())


def test_every_piece_has_fallen_before_the_layer_goes():
    html = confetti.markup()
    durations = [float(d) for d in re.findall(r"animation-duration:([\d.]+)s", html)]
    delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", html)]
    assert len(durations) == len(delays) == confetti.PIECES
    assert max(d + w for d, w in zip(delays, durations)) <= confetti.LENGTH


def test_same_seed_gives_the_same_shower():
    assert confetti.markup(seed=3) == confetti.markup(seed=3)
    assert confetti.markup(seed=3) != confetti.markup(seed=4)


def test_amount_won_is_shown_in_the_middle():
    html = confetti.markup("+15")
    assert '<div class="ci-cf-card"><span>YOU WON</span><b>+15</b><small>COINS</small></div>' in html
    assert "\n" not in html


def test_no_card_without_an_amount():
    assert "ci-cf-card" not in confetti.markup().split("</style>")[1]


def test_amount_is_escaped():
    html = confetti.markup("<img src=x onerror=alert(1)>")
    assert "<img" not in html and "&lt;img" in html


def test_piece_colours_come_from_the_palette():
    pieces = re.findall(r'<span class="ci-cf" style="([^"]+)"', confetti.markup())
    colours = {re.search(r"background:(#[0-9A-F]{6})", style).group(1) for style in pieces}
    assert colours and colours <= set(confetti.COLOURS)


def test_amount_card_is_white_with_an_orange_amount():
    """The card must stand out on the black "You called it" banner as well as on the white page."""
    assert "background:#FFFFFF" in confetti.CSS
    assert f"color:{confetti.AMOUNT_COLOUR}" in confetti.CSS

"""Tests for src/ui/confetti.py: the confetti HTML and which win gets it."""

import re
from decimal import Decimal

from src.models import Bet
from src.service import BetRecord
from src.ui import confetti


def record(bet_id, status="resolved", stake="10", payout=None):
    """Return one of the user's bet records."""
    bet = Bet(id=bet_id, event_id=f"e-{bet_id}", user_id="u1", side="yes", stake=Decimal(stake),
              payout=None if payout is None else Decimal(payout))
    return BetRecord(bet=bet, event_title="Event", event_status=status, outcome="yes")


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
    assert confetti.markup(3) == confetti.markup(3)
    assert confetti.markup(3) != confetti.markup(4)


def test_colours_come_from_the_palette():
    colours = set(re.findall(r"background:(#[0-9A-F]{6})", confetti.markup()))
    assert colours and colours <= set(confetti.COLOURS)


# --- which win gets confetti -------------------------------------------------------

def test_latest_win_gets_confetti_once():
    records = [record("b2", payout="25"), record("b1", payout="0")]
    assert confetti.win_to_celebrate(records, set()) == "b2"
    assert confetti.win_to_celebrate(records, {"b2"}) is None


def test_no_confetti_when_the_latest_settled_bet_lost():
    records = [record("b2", payout="0"), record("b1", payout="25")]
    assert confetti.win_to_celebrate(records, set()) is None


def test_refunds_and_open_bets_are_skipped():
    records = [record("b3", status="open"), record("b2", payout="10"), record("b1", payout="30")]
    assert confetti.win_to_celebrate(records, set()) == "b1"


def test_no_bets_no_confetti():
    assert confetti.win_to_celebrate([], set()) is None

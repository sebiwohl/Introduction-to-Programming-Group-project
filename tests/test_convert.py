"""Tests for src/convert.py: cents, times and dataclass <-> dict round trips."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.convert import (
    ConversionError,
    bet_from_dict,
    bet_to_dict,
    cents_to_money,
    event_from_dict,
    event_to_dict,
    group_from_dict,
    group_to_dict,
    money_to_cents,
    poll_from_dict,
    poll_to_dict,
    text_to_time,
    time_to_text,
    user_from_dict,
    user_to_dict,
)
from src.models import Bet, Event, Group, Poll, User

CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=timezone.utc)


def sample_records():
    """Return one of each record, with payouts that add up to the pool."""
    user = User(id="u1", username="Anna", password_hash="h", group_id="g1",
                role="admin", balance=Decimal("987.50"))
    other = User(id="u2", username="Ben", password_hash="h", group_id="g1",
                 balance=Decimal("1012.50"))
    group = Group(id="g1", name="Friends", admin_id="u1", invite_code="ABC123",
                  start_balance=Decimal("1000"))
    event = Event(id="e1", group_id="g1", creator_id="u1", title="Rain on Saturday",
                  close_time=CLOSE, status="resolved", outcome="no", flags=["u2"])
    bets = [
        Bet(id="b1", event_id="e1", user_id="u1", side="yes",
            stake=Decimal("12.50"), payout=Decimal("0.00")),
        Bet(id="b2", event_id="e1", user_id="u2", side="no",
            stake=Decimal("12.50"), payout=Decimal("25.00")),
    ]
    poll = Poll(id="p1", event_id="e1", opened_by="auto",
                closes_at=CLOSE + timedelta(hours=24),
                votes={"u1": "keep"}, result="kept")
    return [user, other], group, event, bets, poll


# --- money --------------------------------------------------------------------

@pytest.mark.parametrize("amount, cents", [
    (Decimal("12.50"), 1250), (Decimal("0.01"), 1), (Decimal("0"), 0),
    (1000, 100000), (Decimal("1000.00"), 100000),
])
def test_money_to_cents(amount, cents):
    assert money_to_cents(amount) == cents


@pytest.mark.parametrize("cents, amount", [
    (1250, Decimal("12.50")), (1, Decimal("0.01")), (0, Decimal("0.00")),
])
def test_cents_to_money(cents, amount):
    result = cents_to_money(cents)
    assert result == amount
    assert result.as_tuple().exponent == -2


def test_money_round_trip():
    for text in ("0.00", "0.01", "3.33", "12.50", "99999.99"):
        assert cents_to_money(money_to_cents(Decimal(text))) == Decimal(text)


@pytest.mark.parametrize("amount", [
    12.5, True, "12.50", None, Decimal("1.005"), Decimal("NaN"),
])
def test_money_to_cents_rejects_unsafe_values(amount):
    with pytest.raises(ConversionError):
        money_to_cents(amount)


@pytest.mark.parametrize("cents", [12.5, "1250", True, None])
def test_cents_to_money_rejects_non_integers(cents):
    with pytest.raises(ConversionError):
        cents_to_money(cents)


# --- time ---------------------------------------------------------------------

def test_time_round_trip_keeps_offset():
    lisbon_winter = datetime(2026, 11, 20, 19, 0, tzinfo=timezone(timedelta(hours=0)))
    berlin = datetime(2026, 11, 20, 20, 0, tzinfo=timezone(timedelta(hours=1)))
    for moment in (CLOSE, lisbon_winter, berlin):
        assert text_to_time(time_to_text(moment)) == moment
    assert time_to_text(CLOSE) == "2026-11-20T19:00:00+00:00"


def test_time_to_text_rejects_naive_datetime():
    with pytest.raises(ConversionError):
        time_to_text(datetime(2026, 11, 20, 19, 0))


@pytest.mark.parametrize("text", ["2026-11-20T19:00", "not a time", None])
def test_text_to_time_rejects_missing_offset_or_bad_text(text):
    with pytest.raises(ConversionError):
        text_to_time(text)


# --- records ------------------------------------------------------------------

def test_records_round_trip():
    users, group, event, bets, poll = sample_records()
    assert [user_from_dict(user_to_dict(u)) for u in users] == users
    assert group_from_dict(group_to_dict(group)) == group
    assert event_from_dict(event_to_dict(event)) == event
    assert [bet_from_dict(bet_to_dict(b)) for b in bets] == bets
    assert poll_from_dict(poll_to_dict(poll)) == poll


def test_stored_dicts_use_cents_and_iso_times():
    users, group, event, bets, poll = sample_records()
    assert user_to_dict(users[0])["balance"] == 98750
    assert group_to_dict(group)["start_balance"] == 100000
    assert bet_to_dict(bets[1])["stake"] == 1250
    assert bet_to_dict(bets[1])["payout"] == 2500
    assert event_to_dict(event)["close_time"] == "2026-11-20T19:00:00+00:00"


def test_unresolved_bet_keeps_payout_none():
    bet = Bet(id="b1", event_id="e1", user_id="u1", side="yes", stake=Decimal("5"))
    stored = bet_to_dict(bet)
    assert stored["payout"] is None
    assert bet_from_dict(stored).payout is None


def test_stored_dicts_are_json_safe():
    users, group, event, bets, poll = sample_records()
    record = {
        "users": [user_to_dict(u) for u in users], "groups": [group_to_dict(group)],
        "events": [event_to_dict(event)], "bets": [bet_to_dict(b) for b in bets],
        "polls": [poll_to_dict(poll)],
    }
    assert json.loads(json.dumps(record)) == record


def test_to_dict_does_not_share_lists_with_the_record():
    _, _, event, _, poll = sample_records()
    stored = event_to_dict(event)
    stored["flags"].append("u1")
    assert event.flags == ["u2"]
    poll_to_dict(poll)["votes"]["u2"] = "delete"
    assert poll.votes == {"u1": "keep"}


# --- compatibility with storage.py (runs once feature/storage is merged) -------

def test_converted_records_pass_storage_validation():
    storage = pytest.importorskip("src.storage")
    users, group, event, bets, poll = sample_records()
    data = storage.empty_data()
    data["users"] = [user_to_dict(u) for u in users]
    data["groups"] = [group_to_dict(group)]
    data["events"] = [event_to_dict(event)]
    data["bets"] = [bet_to_dict(b) for b in bets]
    data["polls"] = [poll_to_dict(poll)]
    storage.validate(data)  # raises if anything does not match storage's rules

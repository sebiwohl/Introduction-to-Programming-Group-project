"""Tests for src/betting.py: odds, placing bets, refunds, rounding and payouts."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.betting import (
    BettingError,
    can_access_event,
    odds,
    place_bet,
    pool,
    resolve_event,
    split_pool,
    stake_per_side,
    visible_events,
)
from src.models import (
    STATUS_CLOSED,
    STATUS_DELETED,
    STATUS_OPEN,
    STATUS_PAUSED,
    STATUS_RESOLVED,
    Bet,
    Event,
    User,
)

NOW = datetime(2026, 11, 20, 12, 0)  # before close: betting allowed
CLOSE = datetime(2026, 11, 20, 19, 0)
AFTER = CLOSE + timedelta(hours=1)  # after close: resolving allowed


def make_user(user_id="u1", balance=100, group_id="g1"):
    """Return a test user in group g1 with the given balance."""
    return User(id=user_id, username=user_id, password_hash="x",
                group_id=group_id, balance=balance)


def make_event(status=STATUS_OPEN, close_time=CLOSE, group_id="g1", event_id="e1"):
    """Return a test event in group g1."""
    return Event(
        id=event_id, group_id=group_id, creator_id="u1", title="Anna is late",
        close_time=close_time, status=status,
    )


def make_bet(user_id, side, stake, bet_id=None):
    """Return a bet on event e1."""
    return Bet(id=bet_id or f"b-{user_id}", event_id="e1",
               user_id=user_id, side=side, stake=stake)


def example_bets():
    """Section 1.2 example: four 'yes' bets of 10, one 'no' bet of 10."""
    bets = [make_bet(f"u{i}", "yes", 10) for i in range(1, 5)]
    bets.append(make_bet("u5", "no", 10))
    return bets


def users_for(bets, balance=0):
    """Return a user dict with one user per bettor."""
    return {b.user_id: make_user(b.user_id, balance) for b in bets}


# --- pool, stake_per_side, odds -------------------------------------------

def test_example_pool_and_odds():
    bets = example_bets()
    assert pool(bets) == 50
    assert stake_per_side(bets) == {"yes": 40, "no": 10}
    assert odds(bets, "yes") == 1.25
    assert odds(bets, "no") == 5.0


def test_odds_and_pool_with_no_bets():
    assert pool([]) == 0
    assert stake_per_side([]) == {"yes": 0, "no": 0}
    assert odds([], "yes") is None


def test_odds_none_when_side_has_no_stake():
    bets = [make_bet("u1", "yes", 10)]
    assert odds(bets, "no") is None
    assert odds(bets, "yes") == 1.0


def test_odds_use_stake_not_number_of_people():
    bets = [make_bet("u1", "yes", 30), make_bet("u2", "no", 10), make_bet("u3", "no", 10)]
    assert odds(bets, "yes") == pytest.approx(50 / 30)
    assert odds(bets, "no") == 2.5


def test_odds_change_after_new_bet():
    bets = [make_bet("u1", "yes", 10), make_bet("u2", "no", 10)]
    assert odds(bets, "yes") == 2.0
    bets.append(make_bet("u3", "yes", 10))
    assert odds(bets, "yes") == pytest.approx(30 / 20)


def test_odds_rejects_invalid_side():
    with pytest.raises(BettingError):
        odds(example_bets(), "maybe")


# --- place_bet: accepted ---------------------------------------------------

def test_place_bet_deducts_stake_and_returns_bet():
    user, event = make_user(balance=100), make_event()
    bet = place_bet(user, event, "yes", 30, NOW)
    assert user.balance == 70
    assert (bet.event_id, bet.user_id, bet.side, bet.stake) == ("e1", "u1", "yes", 30)
    assert bet.payout is None


def test_place_bet_accepts_two_decimal_stake():
    user = make_user(balance=100)
    bet = place_bet(user, make_event(), "yes", Decimal("12.34"), NOW)
    assert bet.stake == Decimal("12.34")
    assert user.balance == Decimal("87.66")


def test_place_bet_whole_balance_is_allowed():
    user = make_user(balance=50)
    place_bet(user, make_event(), "no", 50, NOW)
    assert user.balance == 0


def test_place_bet_just_before_close_is_allowed():
    user = make_user()
    place_bet(user, make_event(), "yes", 1, CLOSE - timedelta(seconds=1))
    assert user.balance == 99


# --- place_bet: rejected ---------------------------------------------------

@pytest.mark.parametrize("stake", [0, -5, Decimal("0.00"), Decimal("-0.01")])
def test_place_bet_rejects_non_positive_stake(stake):
    user = make_user()
    with pytest.raises(BettingError):
        place_bet(user, make_event(), "yes", stake, NOW)
    assert user.balance == 100


@pytest.mark.parametrize(
    "stake",
    [2.5, "10", None, True, Decimal("2.555"), Decimal("NaN"), Decimal("Infinity")],
)
def test_place_bet_rejects_invalid_stake_value(stake):
    # Floats, strings, bools, more than 2 decimals and non-finite values.
    user = make_user()
    with pytest.raises(BettingError):
        place_bet(user, make_event(), "yes", stake, NOW)
    assert user.balance == 100


def test_place_bet_rejects_stake_above_balance():
    user = make_user(balance=100)
    with pytest.raises(BettingError):
        place_bet(user, make_event(), "yes", 101, NOW)
    assert user.balance == 100


def test_place_bet_rejects_invalid_side():
    user = make_user()
    with pytest.raises(BettingError):
        place_bet(user, make_event(), "maybe", 10, NOW)
    assert user.balance == 100


@pytest.mark.parametrize(
    "status", [STATUS_CLOSED, STATUS_PAUSED, STATUS_RESOLVED, STATUS_DELETED]
)
def test_place_bet_rejects_event_not_open(status):
    user = make_user()
    with pytest.raises(BettingError):
        place_bet(user, make_event(status=status), "yes", 10, NOW)
    assert user.balance == 100


def test_place_bet_rejects_at_and_after_close_time():
    user = make_user()
    for moment in (CLOSE, CLOSE + timedelta(hours=1)):
        with pytest.raises(BettingError):
            place_bet(user, make_event(), "yes", 10, moment)
    assert user.balance == 100


# --- split_pool: rounding and leftover ------------------------------------

def test_split_pool_example_yes_wins_is_exact():
    # 50 split over four equal stakes of 10: exactly 12.50 each.
    assert split_pool(50, [10, 10, 10, 10]) == [Decimal("12.50")] * 4


def test_split_pool_results_have_two_decimals():
    payouts = split_pool(10, [3, 3, 3])
    assert all(p.as_tuple().exponent == -2 for p in payouts)


def test_split_pool_exact_split_has_no_leftover():
    assert split_pool(100, [10, 30, 10]) == [20, 60, 20]
    assert split_pool(10, [1, 1, 2]) == [Decimal("2.50"), Decimal("2.50"), Decimal("5.00")]


def test_split_pool_leftover_cent_goes_to_largest_stake():
    # 0.10 over stakes 1:1:2 -> 0.025, 0.025, 0.05 -> floor 0.02, 0.02, 0.05,
    # leftover 0.01 to the largest stake.
    assert split_pool(Decimal("0.10"), [1, 1, 2]) == [
        Decimal("0.02"), Decimal("0.02"), Decimal("0.06"),
    ]


def test_split_pool_tie_goes_to_earliest():
    # 10 over three equal stakes: 3.33 each, leftover 0.01 to the first.
    assert split_pool(10, [3, 3, 3]) == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]


def test_split_pool_works_with_decimal_stakes():
    payouts = split_pool(Decimal("10.01"), [Decimal("1.50"), Decimal("2.25")])
    assert sum(payouts) == Decimal("10.01")
    assert payouts == [Decimal("4.00"), Decimal("6.01")]


def test_split_pool_always_sums_to_pool():
    totals = (Decimal("0.01"), 1, 7, Decimal("50.05"), 99, Decimal("1000.99"))
    stake_lists = ([1], [1, 2, 3], [7, 7, 7, 7], [13, 1, 1], [Decimal("0.33")] * 3)
    for total in totals:
        for stakes in stake_lists:
            assert sum(split_pool(total, stakes)) == total


def test_split_pool_rejects_no_stakes():
    with pytest.raises(BettingError):
        split_pool(50, [])


# --- resolve_event: payouts ------------------------------------------------

def test_resolve_no_wins_pays_whole_pool_to_single_bettor():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    assert resolve_event(event, bets, "no", users, AFTER) == 50
    payouts = {b.user_id: b.payout for b in bets}
    assert payouts == {"u1": 0, "u2": 0, "u3": 0, "u4": 0, "u5": 50}
    assert users["u5"].balance == 50
    assert users["u1"].balance == 0
    assert (event.status, event.outcome) == (STATUS_RESOLVED, "no")


def test_resolve_yes_wins_splits_pool_and_total_equals_pool():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    assert resolve_event(event, bets, "yes", users, AFTER) == 50
    payouts = [b.payout for b in bets]
    assert payouts == [Decimal("12.50")] * 4 + [Decimal("0.00")]  # section 1.2
    assert sum(payouts) == pool(bets)
    assert sum(u.balance for u in users.values()) == 50


def test_resolve_pays_in_proportion_to_stakes():
    bets = [make_bet("u1", "yes", 30), make_bet("u2", "yes", 10), make_bet("u3", "no", 20)]
    users = users_for(bets)
    resolve_event(make_event(), bets, "yes", users, AFTER)
    assert [b.payout for b in bets] == [45, 15, 0]  # pool 60 split 3:1


def test_resolve_credits_existing_balances():
    bets = [make_bet("u1", "yes", 10), make_bet("u2", "no", 10)]
    users = users_for(bets, balance=90)
    resolve_event(make_event(), bets, "yes", users, AFTER)
    assert users["u1"].balance == 110
    assert users["u2"].balance == 90


def test_resolve_closed_event_is_allowed():
    bets = example_bets()
    resolve_event(make_event(status=STATUS_CLOSED), bets, "no", users_for(bets), AFTER)


# --- resolve_event: refunds -------------------------------------------------

def test_refund_when_only_one_side_has_bets():
    bets = [make_bet("u1", "yes", 10), make_bet("u2", "yes", 25)]
    users = users_for(bets)
    assert resolve_event(make_event(), bets, "yes", users, AFTER) == 35
    assert [b.payout for b in bets] == [10, 25]
    assert users["u1"].balance == 10 and users["u2"].balance == 25


def test_refund_when_nobody_backed_the_winner():
    bets = [make_bet("u1", "yes", 10), make_bet("u2", "yes", 20)]
    users = users_for(bets)
    resolve_event(make_event(), bets, "no", users, AFTER)
    assert [b.payout for b in bets] == [10, 20]
    assert users["u1"].balance == 10 and users["u2"].balance == 20


def test_resolve_with_no_bets_marks_event_resolved():
    event = make_event()
    assert resolve_event(event, [], "yes", {}, AFTER) == 0
    assert (event.status, event.outcome) == (STATUS_RESOLVED, "yes")


# --- resolve_event: read-only and rejected cases ---------------------------

def test_resolved_event_cannot_be_resolved_again():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    resolve_event(event, bets, "no", users, AFTER)
    balances = {uid: u.balance for uid, u in users.items()}
    with pytest.raises(BettingError):
        resolve_event(event, bets, "yes", users, AFTER)
    assert event.outcome == "no"
    assert {uid: u.balance for uid, u in users.items()} == balances


@pytest.mark.parametrize("status", [STATUS_PAUSED, STATUS_DELETED])
def test_cannot_resolve_paused_or_deleted_event(status):
    bets = example_bets()
    with pytest.raises(BettingError):
        resolve_event(make_event(status=status), bets, "yes", users_for(bets), AFTER)
    assert all(b.payout is None for b in bets)


def test_resolve_rejects_invalid_outcome_and_changes_nothing():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    with pytest.raises(BettingError):
        resolve_event(event, bets, "maybe", users, AFTER)
    assert event.status == STATUS_OPEN and event.outcome is None
    assert all(b.payout is None for b in bets)


def test_resolve_rejects_bet_from_other_event_and_changes_nothing():
    event, bets = make_event(), example_bets()
    bets[-1].event_id = "other"
    users = users_for(bets)
    with pytest.raises(BettingError):
        resolve_event(event, bets, "yes", users, AFTER)
    assert event.status == STATUS_OPEN
    assert all(b.payout is None for b in bets)
    assert all(u.balance == 0 for u in users.values())


def test_resolve_rejects_unknown_user_and_changes_nothing():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    del users["u3"]
    with pytest.raises(BettingError):
        resolve_event(event, bets, "yes", users, AFTER)
    assert event.status == STATUS_OPEN
    assert all(b.payout is None for b in bets)


# --- end to end: coins are conserved --------------------------------------

def test_full_flow_total_coins_conserved():
    event = make_event()
    users = {f"u{i}": make_user(f"u{i}", 100) for i in range(1, 6)}
    start_total = sum(u.balance for u in users.values())
    bets = []
    for uid, side, stake in [("u1", "yes", Decimal("7.33")), ("u2", "yes", 13),
                             ("u3", "no", Decimal("9.99")), ("u4", "no", 31),
                             ("u5", "yes", Decimal("0.01"))]:
        bets.append(place_bet(users[uid], event, side, stake, NOW))
    assert sum(u.balance for u in users.values()) == start_total - pool(bets)
    resolve_event(event, bets, "yes", users, AFTER)
    assert sum(u.balance for u in users.values()) == start_total
    assert sum(b.payout for b in bets) == pool(bets)


# --- group access -----------------------------------------------------------

def test_can_access_event_only_in_own_group():
    user = make_user(group_id="g1")
    assert can_access_event(user, make_event(group_id="g1"))
    assert not can_access_event(user, make_event(group_id="g2"))


def test_user_without_group_cannot_access_any_event():
    user = make_user(group_id=None)
    assert not can_access_event(user, make_event(group_id="g1"))


def test_visible_events_hides_other_groups():
    own = make_event(group_id="g1", event_id="e1")
    other = make_event(group_id="g2", event_id="e2")
    also_own = make_event(group_id="g1", event_id="e3")
    events = visible_events(make_user(group_id="g1"), [own, other, also_own])
    assert [e.id for e in events] == ["e1", "e3"]


def test_place_bet_rejects_event_from_other_group():
    user = make_user(balance=100, group_id="g1")
    with pytest.raises(BettingError):
        place_bet(user, make_event(group_id="g2"), "yes", 10, NOW)
    assert user.balance == 100


# --- resolve_event: timing ----------------------------------------------------

def test_cannot_resolve_before_close_time_and_changes_nothing():
    event, bets = make_event(), example_bets()
    users = users_for(bets)
    with pytest.raises(BettingError):
        resolve_event(event, bets, "yes", users, CLOSE - timedelta(seconds=1))
    assert event.status == STATUS_OPEN and event.outcome is None
    assert all(b.payout is None for b in bets)
    assert all(u.balance == 0 for u in users.values())


def test_can_resolve_exactly_at_close_time():
    event, bets = make_event(), example_bets()
    assert resolve_event(event, bets, "no", users_for(bets), CLOSE) == 50
    assert event.status == STATUS_RESOLVED

"""Tests for src/balances.py and the balance-0 block on creating events."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, notifications, service, storage
from src.auth import AuthError
from src.models import User

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLOSE = NOW + timedelta(days=2)
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Use few hash iterations so the tests run fast (real code uses 600,000)."""
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 1_000)
    monkeypatch.setattr(auth, "_dummy_hash", None)


@pytest.fixture
def path(tmp_path):
    """Return the path of an empty, temporary data file."""
    return tmp_path / "data.json"


@pytest.fixture
def group(path):
    """Return (anna = admin, ben, carl = members, dan = outsider, group id).

    Everyone joined with 0, then the admin topped up 100 an hour ago.
    """
    ids = [auth.sign_up(name, PASSWORD, path).id for name in ("anna", "ben", "carl", "dan")]
    friends = auth.create_group(ids[0], "Friends", path=path)
    for user_id in ids[1:3]:
        auth.join_group(user_id, friends.invite_code, path)
    balances.top_up(ids[0], friends.id, 100, NOW - timedelta(hours=1), path)
    return (*ids, friends.id)


def balance(path, user_id, group_id):
    """Return the user's balance in the group."""
    return auth.user_in_group(storage.load_data(path), user_id, group_id).balance


def notes(path, user_id):
    """Return the texts of the user's notifications, newest first."""
    return [n.text for n in notifications.notifications_for(user_id, path)
            if not notifications.is_activity(n)]  # personal ones, not the activity log


# --- top-ups --------------------------------------------------------------------

def test_top_up_gives_every_member_the_same_and_is_recorded(path, group):
    anna, ben, carl, _, group_id = group
    topup = balances.top_up(anna, group_id, Decimal("50.25"), NOW, path)
    assert [balance(path, u, group_id) for u in (anna, ben, carl)] == [Decimal("150.25")] * 3
    stored = storage.load_data(path)["topups"][-1]
    assert stored == {"id": topup.id, "group_id": group_id, "amount": 5025,
                      "created_by": anna, "created_at": NOW.isoformat()}


def test_restricted_members_get_the_top_up_too(path, group):
    anna, ben, _, _, group_id = group
    members.set_restricted(anna, group_id, ben, True, NOW, path)
    balances.top_up(anna, group_id, 20, NOW, path)
    assert balance(path, ben, group_id) == Decimal("120.00")


def test_top_up_leaves_other_groups_alone(path, group):
    anna, ben, _, dan, group_id = group
    other = auth.create_group(dan, "Others", path=path)
    auth.join_group(ben, other.invite_code, path)
    balances.top_up(dan, other.id, 100, NOW, path)
    balances.top_up(anna, group_id, 30, NOW, path)
    assert balance(path, ben, other.id) == Decimal("100.00")


def test_member_cannot_top_up(path, group):
    anna, ben, _, _, group_id = group
    with pytest.raises(AuthError, match="Only the group admin"):
        balances.top_up(ben, group_id, 50, NOW, path)
    assert len(storage.load_data(path)["topups"]) == 1  # only the fixture's top-up
    assert balance(path, ben, group_id) == Decimal("100.00")


def test_outsider_cannot_top_up(path, group):
    _, _, _, dan, group_id = group
    with pytest.raises(AuthError, match="Group not found"):
        balances.top_up(dan, group_id, 50, NOW, path)


@pytest.mark.parametrize("amount", [0, -5, Decimal("0.001"), Decimal("NaN"), 1.5, True, "50", None])
def test_bad_top_up_amount_is_rejected_and_nothing_changes(path, group, amount):
    anna, _, _, _, group_id = group
    with pytest.raises(AuthError, match="Top-up"):
        balances.top_up(anna, group_id, amount, NOW, path)
    assert len(storage.load_data(path)["topups"]) == 1  # only the fixture's top-up


def test_smallest_top_up_is_one_cent(path, group):
    anna, _, _, _, group_id = group
    balances.top_up(anna, group_id, Decimal("0.01"), NOW, path)
    assert balance(path, anna, group_id) == Decimal("100.01")


def test_last_top_up_and_low_line(path, group):
    anna, _, _, dan, group_id = group
    fresh = auth.create_group(dan, "Fresh", path=path)
    data = storage.load_data(path)
    assert balances.last_top_up(data, fresh.id) is None
    assert balances.low_balance_line(data, fresh.id) is None
    balances.top_up(anna, group_id, 500, NOW, path)
    balances.top_up(anna, group_id, 200, NOW + timedelta(hours=1), path)
    data = storage.load_data(path)
    assert balances.last_top_up(data, group_id).amount == Decimal("200.00")
    assert balances.low_balance_line(data, group_id) == Decimal("20.00")


# --- creating events at a balance of 0 ------------------------------------------

def test_member_at_zero_cannot_create_events():
    broke = User("u1", "anna", "h", group_id="g1", balance=Decimal("0"))
    with pytest.raises(AuthError, match="Top-up required before creating events"):
        auth.require_permission(broke, auth.CREATE_EVENT)
    auth.require_permission(broke, auth.PLACE_BET)  # other actions are not blocked by this rule
    auth.require_permission(broke, auth.FLAG_EVENT)


def test_one_cent_is_enough_to_create_events():
    auth.require_permission(User("u1", "anna", "h", group_id="g1", balance=Decimal("0.01")),
                            auth.CREATE_EVENT)


def test_spending_everything_blocks_creating_until_top_up(path, group):
    anna, ben, _, _, group_id = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ben, event.id, "yes", Decimal("100"), NOW, path)
    with pytest.raises(AuthError, match="Top-up required"):
        service.create_event(ben, "Snow", CLOSE, NOW, path=path)
    assert len(storage.load_data(path)["events"]) == 1
    balances.top_up(anna, group_id, 10, NOW, path)
    service.create_event(ben, "Snow", CLOSE, NOW, path=path)


# --- running low ----------------------------------------------------------------

@pytest.fixture
def topped(path, group):
    """The group after a top-up of 500 (balances 600, low line 50); returns (group, event id)."""
    anna, *_, group_id = group
    balances.top_up(anna, group_id, 500, NOW, path)
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    return group, event.id


def test_admin_is_notified_when_a_bet_crosses_the_line(path, topped):
    (anna, ben, _, _, group_id), event_id = topped
    service.place_bet(ben, event_id, "yes", Decimal("551"), NOW, path)  # 600 -> 49
    assert notes(path, anna) == [
        "ben is running low in Friends (below 50.00 coins). Choose a top-up or a repayment."]
    assert notes(path, ben) == []


def test_no_alert_while_staying_at_or_above_the_line(path, topped):
    (anna, ben, _, _, _), event_id = topped
    service.place_bet(ben, event_id, "yes", Decimal("550"), NOW, path)  # 600 -> 50, not below
    assert notes(path, anna) == []


def test_alert_only_once_when_already_below(path, topped):
    (anna, ben, _, _, _), event_id = topped
    service.place_bet(ben, event_id, "yes", Decimal("560"), NOW, path)  # -> 40
    service.place_bet(ben, event_id, "no", Decimal("10"), NOW, path)    # -> 30
    assert len(notes(path, anna)) == 1


def test_alert_again_after_a_top_up_and_a_new_drop(path, topped):
    (anna, ben, _, _, group_id), event_id = topped
    service.place_bet(ben, event_id, "yes", Decimal("560"), NOW, path)  # -> 40, alert
    balances.top_up(anna, group_id, 100, NOW, path)                     # -> 140, line 10
    service.place_bet(ben, event_id, "yes", Decimal("135"), NOW, path)  # -> 5, alert
    assert len(notes(path, anna)) == 2


def test_admin_is_notified_about_themselves_too(path, topped):
    (anna, _, _, _, _), event_id = topped
    service.place_bet(anna, event_id, "no", Decimal("580"), NOW, path)
    assert len(notes(path, anna)) == 1


def test_no_alert_without_any_top_up(path, group):
    _, _, _, dan, _ = group
    fresh = auth.create_group(dan, "Fresh", path=path)
    data = storage.load_data(path)
    before = len(data["notifications"])  # the setup's activity log entries
    assert balances.notify_if_low(data, fresh.id, dan, Decimal("10"), NOW) is False
    assert len(data["notifications"]) == before


# --- received this period, result and debt (schema version 3) ---------------------

def received(path, user_id, group_id):
    """Return what the user received this period in the group."""
    return auth.user_in_group(storage.load_data(path), user_id, group_id).received


def test_new_group_starts_everyone_at_zero(path):
    anna = auth.sign_up("zoe", PASSWORD, path).id
    group = auth.create_group(anna, "New", path=path)
    member = auth.user_in_group(storage.load_data(path), anna, group.id)
    assert (member.balance, member.received) == (0, 0)
    with pytest.raises(AuthError, match="Top-up required"):
        service.create_event(anna, "Rain", CLOSE, NOW, path=path)


def test_top_up_counts_as_received(path, group):
    anna, ben, _, _, group_id = group
    balances.top_up(anna, group_id, 50, NOW, path)
    assert received(path, ben, group_id) == Decimal("150.00")


def test_member_joining_mid_period_gets_only_later_top_ups(path, group):
    anna, _, _, dan, group_id = group
    code = next(g["invite_code"] for g in storage.load_data(path)["groups"])
    auth.join_group(dan, code, path)
    assert (balance(path, dan, group_id), received(path, dan, group_id)) == (0, 0)
    balances.top_up(anna, group_id, 40, NOW, path)
    assert (balance(path, dan, group_id), received(path, dan, group_id)) == (40, 40)


def test_result_and_debt_follow_balance_against_received(path, group):
    anna, ben, carl, _, group_id = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ben, event.id, "yes", Decimal("30"), NOW, path)
    service.place_bet(carl, event.id, "no", Decimal("10"), NOW, path)
    service.resolve_event(anna, event.id, "yes", CLOSE + timedelta(hours=1), path)
    data = storage.load_data(path)
    ben_m, carl_m, anna_m = (auth.user_in_group(data, u, group_id) for u in (ben, carl, anna))
    assert [balances.period_result(m) for m in (ben_m, carl_m, anna_m)] == [
        Decimal("10.00"), Decimal("-10.00"), Decimal("0.00")]
    assert [balances.has_debt(m) for m in (ben_m, carl_m, anna_m)] == [False, True, False]


def test_open_stakes_are_not_lost_until_settled(path, group):
    """Decided: an open stake still counts as the member's (it carries over at Repaid)."""
    anna, ben, _, _, group_id = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ben, event.id, "yes", Decimal("30"), NOW, path)
    data = storage.load_data(path)
    member = auth.user_in_group(data, ben, group_id)
    stake = Decimal(balances.open_stakes(data, group_id)[ben]) / 100
    assert balances.period_result(member, stake) == 0
    assert not balances.has_debt(member, stake)
    assert balances.period_result(member) == Decimal("-30.00")  # without the open stake

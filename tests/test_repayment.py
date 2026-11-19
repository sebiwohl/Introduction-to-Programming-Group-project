"""Tests for the repayment: results, simplified transfers, the copy text and "Repaid"."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, notifications, service, storage
from src.auth import AuthError
from src.balances import MemberResult

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLOSE = NOW + timedelta(days=2)
AFTER = CLOSE + timedelta(hours=1)
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
    """anna (admin), ben, carl; everyone topped up 100. Returns (ids dict, group id)."""
    ids = {name: auth.sign_up(name, PASSWORD, path).id for name in ("anna", "ben", "carl", "dan")}
    friends = auth.create_group(ids["anna"], "Friends", path=path)
    for name in ("ben", "carl"):
        auth.join_group(ids[name], friends.invite_code, path)
    balances.top_up(ids["anna"], friends.id, 100, NOW - timedelta(hours=1), path)
    return ids, friends.id


def bet_and_resolve(path, ids, winner_side="yes", stakes=None):
    """anna's event: ben 40 yes, carl 20 no (or `stakes`); resolve with winner_side."""
    event = service.create_event(ids["anna"], "Rain", CLOSE, NOW, path=path)
    for name, side, stake in stakes or (("ben", "yes", "40"), ("carl", "no", "20")):
        service.place_bet(ids[name], event.id, side, Decimal(stake), NOW, path)
    service.resolve_event(ids["anna"], event.id, winner_side, AFTER, path)
    return event.id


def results(summary):
    """Return {username: result} from a summary."""
    return {r.username: r.result for r in summary.results}


def membership(path, user_id, group_id):
    """Return the stored membership dict."""
    return next(m for m in storage.load_data(path)["memberships"]
                if m["user_id"] == user_id and m["group_id"] == group_id)


# --- the window -----------------------------------------------------------------

def test_window_shows_results_and_transfers(path, group):
    ids, group_id = group
    bet_and_resolve(path, ids)  # ben wins carl's 20
    summary = balances.repayment_summary(ids["carl"], group_id, path)
    assert results(summary) == {"ben": Decimal("20.00"), "anna": 0, "carl": Decimal("-20.00")}
    assert [(t.payer, t.receiver, t.amount) for t in summary.transfers] == [
        ("carl", "ben", Decimal("20.00"))]
    assert summary.text == "Called it! repayment for Friends:\n- carl pays ben 20.00 coins"


def test_results_always_add_up_to_zero(path, group):
    ids, group_id = group
    bet_and_resolve(path, ids)
    open_event = service.create_event(ids["ben"], "Snow", CLOSE, NOW, path=path)
    service.place_bet(ids["carl"], open_event.id, "yes", Decimal("33.33"), NOW, path)
    summary = balances.repayment_summary(ids["anna"], group_id, path)
    assert sum(r.result for r in summary.results) == 0
    carl = next(r for r in summary.results if r.username == "carl")
    assert (carl.open_stake, carl.result) == (Decimal("33.33"), Decimal("-20.00"))


def test_everyone_even_gives_no_transfers(path, group):
    ids, group_id = group
    summary = balances.repayment_summary(ids["ben"], group_id, path)
    assert summary.transfers == []
    assert "everyone is even" in summary.text


def test_opening_the_window_changes_nothing(path, group):
    ids, group_id = group
    bet_and_resolve(path, ids)
    before = storage.load_data(path)
    balances.repayment_summary(ids["anna"], group_id, path)
    assert storage.load_data(path) == before


def test_restricted_member_can_still_see_the_window(path, group):
    ids, group_id = group
    members.set_restricted(ids["anna"], group_id, ids["carl"], True, NOW, path)
    assert balances.repayment_summary(ids["carl"], group_id, path).results


def test_outsider_cannot_see_the_window(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Group not found"):
        balances.repayment_summary(ids["dan"], group_id, path)


# --- simplified transfers ---------------------------------------------------------

def result(name, amount):
    """Return a MemberResult with only a name and a result."""
    return MemberResult(name, name, Decimal("0"), Decimal("0"), Decimal("0"), Decimal(amount))


def test_transfers_settle_everyone_with_fewer_payments_than_members():
    rows = [result("a", "50"), result("b", "30"), result("c", "-45"), result("d", "-35")]
    transfers = balances.simplify_transfers(rows)
    assert [(t.payer, t.receiver, t.amount) for t in transfers] == [
        ("c", "a", Decimal("45")), ("d", "a", Decimal("5")), ("d", "b", Decimal("30"))]
    net = {}
    for t in transfers:
        net[t.payer] = net.get(t.payer, 0) - t.amount
        net[t.receiver] = net.get(t.receiver, 0) + t.amount
    assert net == {"a": 50, "b": 30, "c": -45, "d": -35}


def test_transfers_with_cents_and_zero_results():
    rows = [result("a", "0.01"), result("b", "0"), result("c", "-0.01")]
    assert [(t.payer, t.receiver, t.amount) for t in balances.simplify_transfers(rows)] == [
        ("c", "a", Decimal("0.01"))]


# --- Repaid -----------------------------------------------------------------------

def test_repaid_resets_balances_and_starts_a_new_period(path, group):
    ids, group_id = group
    bet_and_resolve(path, ids)
    balances.confirm_repaid(ids["anna"], group_id, 50, AFTER, path)
    for name in ("anna", "ben", "carl"):
        m = membership(path, ids[name], group_id)
        assert (m["balance"], m["received"]) == (5000, 5000)
    data = storage.load_data(path)
    assert [r["created_by"] for r in data["repayments"]] == [ids["anna"]]
    assert balances.last_top_up(data, group_id).amount == Decimal("50.00")
    summary = balances.repayment_summary(ids["anna"], group_id, path)
    assert summary.transfers == [] and all(r.result == 0 for r in summary.results)


def test_open_bets_carry_over_into_the_new_period(path, group):
    ids, group_id = group
    event = service.create_event(ids["anna"], "Snow", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("30"), NOW, path)
    service.place_bet(ids["carl"], event.id, "no", Decimal("10"), NOW, path)
    balances.confirm_repaid(ids["anna"], group_id, 50, NOW, path)
    ben = membership(path, ids["ben"], group_id)
    assert (ben["balance"], ben["received"]) == (5000, 8000)  # the 30 stake moved along
    assert balances.repayment_summary(ids["anna"], group_id, path).transfers == []
    service.resolve_event(ids["anna"], event.id, "yes", AFTER, path)  # ben wins carl's 10
    after = results(balances.repayment_summary(ids["anna"], group_id, path))
    assert after == {"ben": Decimal("10.00"), "anna": 0, "carl": Decimal("-10.00")}


def test_everyone_is_notified_of_the_new_period(path, group):
    ids, group_id = group
    balances.confirm_repaid(ids["anna"], group_id, 50, NOW, path)
    for name in ("anna", "ben", "carl"):
        [note] = notifications.notifications_for(ids[name], path)
        assert note.text == "Repaid: a new period started. Everyone got 50.00 coins."


def test_member_cannot_confirm_repaid(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Only the group admin"):
        balances.confirm_repaid(ids["ben"], group_id, 50, NOW, path)
    assert storage.load_data(path)["repayments"] == []
    assert membership(path, ids["ben"], group_id)["balance"] == 10000


def test_outsider_cannot_confirm_repaid(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Group not found"):
        balances.confirm_repaid(ids["dan"], group_id, 50, NOW, path)


@pytest.mark.parametrize("amount", [0, -5, Decimal("0.001"), 1.5, True, "50", None])
def test_repaid_needs_a_top_up_above_zero(path, group, amount):
    ids, group_id = group
    with pytest.raises(AuthError, match="Top-up"):
        balances.confirm_repaid(ids["anna"], group_id, amount, NOW, path)
    assert storage.load_data(path)["repayments"] == []


def test_repaid_in_one_group_leaves_other_groups_alone(path, group):
    ids, group_id = group
    other = auth.create_group(ids["dan"], "Others", path=path)
    auth.join_group(ids["ben"], other.invite_code, path)
    balances.top_up(ids["dan"], other.id, 70, NOW, path)
    balances.confirm_repaid(ids["anna"], group_id, 50, NOW, path)
    assert membership(path, ids["ben"], other.id)["balance"] == 7000


def test_leaderboard_result_includes_open_stakes(path, group):
    ids, group_id = group
    event = service.create_event(ids["anna"], "Snow", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("30"), NOW, path)
    rows = {r.username: r.result for r in service.page_data(ids["ben"], NOW, path).leaderboard}
    assert rows["ben"] == 0

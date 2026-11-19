"""Tests for leaving a group and removing a member (src/members.py)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, notifications, service, storage
from src.auth import AuthError

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
    """anna (admin), ben, carl in Friends, topped up 100; dan is outside. Returns (ids, group id)."""
    ids = {name: auth.sign_up(name, PASSWORD, path).id for name in ("anna", "ben", "carl", "dan")}
    friends = auth.create_group(ids["anna"], "Friends", path=path)
    for name in ("ben", "carl"):
        auth.join_group(ids[name], friends.invite_code, path)
    balances.top_up(ids["anna"], friends.id, 100, NOW - timedelta(hours=1), path)
    clear_notifications(path)  # the group's activity log noted the joins and the top-up
    return ids, friends.id


def clear_notifications(path):
    """Start a test with no notifications (setting up the group logs joins and top-ups)."""
    with storage.transaction(path) as data:
        data["notifications"] = []



def in_group(path, user_id, group_id):
    """Return True if the user still has a membership in the group."""
    return any(m["user_id"] == user_id and m["group_id"] == group_id
               for m in storage.load_data(path)["memberships"])


def ben_wins_from_carl(path, ids):
    """anna's event, ben 20 yes, carl 20 no, resolved yes: ben +20, carl -20."""
    event = service.create_event(ids["anna"], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("20"), NOW, path)
    service.place_bet(ids["carl"], event.id, "no", Decimal("20"), NOW, path)
    service.resolve_event(ids["anna"], event.id, "yes", AFTER, path)


# --- leaving --------------------------------------------------------------------

def test_settled_member_leaves_and_the_admin_is_told(path, group):
    ids, group_id = group
    members.leave_group(ids["ben"], group_id, NOW, path)
    assert not in_group(path, ids["ben"], group_id)
    [note] = notifications.notifications_for(ids["anna"], path)
    assert note.text == "ben left Friends."


def test_cannot_leave_with_open_bets(path, group):
    ids, group_id = group
    event = service.create_event(ids["anna"], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("10"), NOW, path)
    with pytest.raises(AuthError, match="You still have open bets"):
        members.leave_group(ids["ben"], group_id, NOW, path)
    assert in_group(path, ids["ben"], group_id)


def test_cannot_leave_with_debt(path, group):
    ids, group_id = group
    ben_wins_from_carl(path, ids)
    with pytest.raises(AuthError, match="You are down 20.00 coins"):
        members.leave_group(ids["carl"], group_id, AFTER, path)


def test_cannot_leave_with_winnings(path, group):
    """Decided: leaving needs a result of exactly 0, so repayments still add up."""
    ids, group_id = group
    ben_wins_from_carl(path, ids)
    with pytest.raises(AuthError, match="You are up 20.00 coins"):
        members.leave_group(ids["ben"], group_id, AFTER, path)


def test_after_repayment_everyone_can_leave(path, group):
    ids, group_id = group
    ben_wins_from_carl(path, ids)
    balances.confirm_repaid(ids["anna"], group_id, 50, AFTER, path)
    members.leave_group(ids["ben"], group_id, AFTER, path)
    members.leave_group(ids["carl"], group_id, AFTER, path)
    assert balances.repayment_summary(ids["anna"], group_id, path).transfers == []


def test_restricted_member_can_still_leave_when_settled(path, group):
    ids, group_id = group
    members.set_restricted(ids["anna"], group_id, ids["ben"], True, NOW, path)
    members.leave_group(ids["ben"], group_id, NOW, path)
    assert not in_group(path, ids["ben"], group_id)


def test_admin_must_hand_over_before_leaving(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Hand over the admin role"):
        members.leave_group(ids["anna"], group_id, NOW, path)
    auth.hand_over_admin(ids["anna"], group_id, ids["ben"], NOW, path)
    members.leave_group(ids["anna"], group_id, NOW, path)
    assert not in_group(path, ids["anna"], group_id)


def test_last_member_admin_is_told_to_delete_instead(path, group):
    ids, group_id = group
    for name in ("ben", "carl"):
        members.leave_group(ids[name], group_id, NOW, path)
    with pytest.raises(AuthError, match="delete the group instead"):
        members.leave_group(ids["anna"], group_id, NOW, path)
    members.delete_group(ids["anna"], group_id, path)
    assert storage.load_data(path)["groups"] == []


def test_outsider_cannot_leave(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Group not found"):
        members.leave_group(ids["dan"], group_id, NOW, path)


def test_leaving_one_group_keeps_the_others(path, group):
    ids, group_id = group
    other = auth.create_group(ids["dan"], "Others", path=path)
    auth.join_group(ids["ben"], other.invite_code, path)
    members.leave_group(ids["ben"], group_id, NOW, path)
    assert in_group(path, ids["ben"], other.id)


def test_member_who_left_can_join_again_at_zero(path, group):
    ids, group_id = group
    code = storage.load_data(path)["groups"][0]["invite_code"]
    members.leave_group(ids["ben"], group_id, NOW, path)
    auth.join_group(ids["ben"], code, path)
    member = auth.user_in_group(storage.load_data(path), ids["ben"], group_id)
    assert (member.balance, member.received) == (0, 0)


# --- removing -------------------------------------------------------------------

def test_admin_removes_a_settled_member_who_is_told(path, group):
    ids, group_id = group
    members.remove_member(ids["anna"], group_id, ids["carl"], NOW, path)
    assert not in_group(path, ids["carl"], group_id)
    [note] = notifications.notifications_for(ids["carl"], path)
    assert note.text == "The admin removed you from Friends."


def test_cannot_remove_with_open_bets_but_restrict_then_remove(path, group):
    ids, group_id = group
    event = service.create_event(ids["anna"], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("10"), NOW, path)
    service.place_bet(ids["carl"], event.id, "no", Decimal("10"), NOW, path)
    with pytest.raises(AuthError, match="ben still has open bets"):
        members.remove_member(ids["anna"], group_id, ids["ben"], NOW, path)
    members.set_restricted(ids["anna"], group_id, ids["ben"], True, NOW, path)
    service.resolve_event(ids["anna"], event.id, "yes", AFTER, path)  # ben +10, carl -10
    with pytest.raises(AuthError, match="ben is up 10.00 coins"):
        members.remove_member(ids["anna"], group_id, ids["ben"], AFTER, path)
    balances.confirm_repaid(ids["anna"], group_id, 50, AFTER, path)
    members.remove_member(ids["anna"], group_id, ids["ben"], AFTER, path)
    assert not in_group(path, ids["ben"], group_id)


def test_cannot_remove_a_member_with_debt(path, group):
    ids, group_id = group
    ben_wins_from_carl(path, ids)
    with pytest.raises(AuthError, match="carl is down 20.00 coins"):
        members.remove_member(ids["anna"], group_id, ids["carl"], AFTER, path)


def test_member_cannot_remove(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Only the group admin"):
        members.remove_member(ids["ben"], group_id, ids["carl"], NOW, path)
    assert in_group(path, ids["carl"], group_id)


def test_admin_cannot_remove_themselves(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="cannot remove themselves"):
        members.remove_member(ids["anna"], group_id, ids["anna"], NOW, path)


@pytest.mark.parametrize("target", ["dan", "ghost"])
def test_cannot_remove_someone_outside_the_group(path, group, target):
    ids, group_id = group
    with pytest.raises(AuthError, match="not a member"):
        members.remove_member(ids["anna"], group_id, ids.get(target, "ghost"), NOW, path)


def test_admin_of_another_group_cannot_remove(path, group):
    ids, group_id = group
    auth.create_group(ids["dan"], "Others", path=path)
    with pytest.raises(AuthError, match="Group not found"):
        members.remove_member(ids["dan"], group_id, ids["ben"], NOW, path)


# --- member list for the admin form --------------------------------------------

def test_group_members_lists_everyone_sorted(path, group):
    ids, group_id = group
    members.set_restricted(ids["anna"], group_id, ids["carl"], True, NOW, path)
    listed = members.group_members(ids["ben"], group_id, path)
    assert [(m.username, m.role, m.restricted) for m in listed] == [
        ("anna", "admin", False), ("ben", "member", False), ("carl", "member", True)]


def test_outsider_cannot_list_members(path, group):
    ids, group_id = group
    with pytest.raises(AuthError, match="Group not found"):
        members.group_members(ids["dan"], group_id, path)

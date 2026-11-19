"""Tests for several groups per user and handing over the admin role (src/auth.py)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src import auth, balances, notifications, service, storage
from src.auth import AuthError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
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
    """Return (anna = admin, ben = member, carl = not in it, the group)."""
    anna = auth.sign_up("anna", PASSWORD, path).id
    ben = auth.sign_up("ben", PASSWORD, path).id
    carl = auth.sign_up("carl", PASSWORD, path).id
    friends = auth.create_group(anna, "Friends", path=path)
    auth.join_group(ben, friends.invite_code, path)
    balances.top_up(anna, friends.id, 500, NOW, path)
    clear_notifications(path)  # the group's activity log noted the join and the top-up
    return anna, ben, carl, friends


def clear_notifications(path):
    """Start a test with no notifications (setting up the group logs joins and top-ups)."""
    with storage.transaction(path) as data:
        data["notifications"] = []


def topped_group(admin_id, name, amount, path):
    """Create a group and top it up (only the admin is in it yet); return the group."""
    group = auth.create_group(admin_id, name, path=path)
    balances.top_up(admin_id, group.id, amount, NOW, path)
    return group


def roles(path, group_id):
    """Return {user id: role} for the group's stored memberships."""
    data = storage.load_data(path)
    return {m["user_id"]: m["role"] for m in data["memberships"] if m["group_id"] == group_id}


def stored_admin_id(path, group_id):
    """Return the admin_id stored on the group."""
    return next(g for g in storage.load_data(path)["groups"] if g["id"] == group_id)["admin_id"]


# --- several groups per user ---------------------------------------------------

def test_user_can_create_a_second_group(path, group):
    anna, _, _, friends = group
    work = topped_group(anna, "Work", 200, path)
    pairs = auth.user_groups(anna, path)
    assert [(g.id, m.role, m.balance) for g, m in pairs] == [
        (friends.id, "admin", Decimal("500.00")), (work.id, "admin", Decimal("200.00"))]


def test_user_can_join_a_second_group(path, group):
    anna, _, carl, friends = group
    other = topped_group(carl, "Others", 300, path)
    auth.join_group(anna, other.invite_code, path)
    pairs = {g.name: (m.role, m.balance) for g, m in auth.user_groups(anna, path)}
    assert pairs == {"Friends": ("admin", Decimal("500.00")),
                     "Others": ("member", Decimal("0.00"))}  # joined after the top-up


def test_cannot_join_the_same_group_twice(path, group):
    anna, ben, _, friends = group
    for user in (anna, ben):
        with pytest.raises(AuthError, match="already in this group"):
            auth.join_group(user, friends.invite_code, path)
    assert len(storage.load_data(path)["memberships"]) == 2


def test_invalid_code_is_reported_before_membership(path, group):
    anna = group[0]
    with pytest.raises(AuthError, match="invite code"):
        auth.join_group(anna, "WRONG1", path)


def test_user_groups_is_empty_without_groups(path, group):
    assert auth.user_groups(group[2], path) == []


def test_user_groups_sorted_by_name(path, group):
    anna = group[0]
    auth.create_group(anna, "alpha", path=path)
    auth.create_group(anna, "Zeta", path=path)
    assert [g.name for g, _ in auth.user_groups(anna, path)] == ["alpha", "Friends", "Zeta"]


def test_balance_changes_stay_in_their_own_group(path, group):
    anna, _, _, friends = group
    topped_group(anna, "Work", 200, path)
    close = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    event = service.create_event(anna, "Rain tomorrow", close, NOW, path=path)
    service.place_bet(anna, event.id, "yes", Decimal("50"), NOW, path)
    balances = {g.name: m.balance for g, m in auth.user_groups(anna, path)}
    assert balances == {"Friends": Decimal("450.00"), "Work": Decimal("200.00")}


# --- handing over the admin role ------------------------------------------------

def test_admin_hands_over_to_a_member(path, group):
    anna, ben, _, friends = group
    auth.hand_over_admin(anna, friends.id, ben, NOW, path)
    assert roles(path, friends.id) == {anna: "member", ben: "admin"}
    assert stored_admin_id(path, friends.id) == ben


def test_new_admin_is_notified(path, group):
    anna, ben, _, friends = group
    auth.hand_over_admin(anna, friends.id, ben, NOW, path)
    [note] = notifications.notifications_for(ben, path)
    assert (note.text, note.group_id, note.read) == (
        "You are now the admin of Friends.", friends.id, False)
    assert notifications.notifications_for(anna, path) == []


def test_new_admin_can_hand_it_back(path, group):
    anna, ben, _, friends = group
    auth.hand_over_admin(anna, friends.id, ben, NOW, path)
    auth.hand_over_admin(ben, friends.id, anna, NOW, path)
    assert roles(path, friends.id) == {anna: "admin", ben: "member"}


def test_member_cannot_hand_over(path, group):
    anna, ben, _, friends = group
    with pytest.raises(AuthError, match="Only the group admin"):
        auth.hand_over_admin(ben, friends.id, ben, NOW, path)
    assert roles(path, friends.id) == {anna: "admin", ben: "member"}


def test_old_admin_loses_the_right_to_hand_over(path, group):
    anna, ben, _, friends = group
    auth.hand_over_admin(anna, friends.id, ben, NOW, path)
    with pytest.raises(AuthError, match="Only the group admin"):
        auth.hand_over_admin(anna, friends.id, anna, NOW, path)


def test_outsider_cannot_hand_over_and_learns_nothing(path, group):
    anna, ben, carl, friends = group
    with pytest.raises(AuthError, match="Group not found"):
        auth.hand_over_admin(carl, friends.id, carl, NOW, path)
    with pytest.raises(AuthError, match="Group not found"):
        auth.hand_over_admin(anna, "no-such-group", ben, NOW, path)


def test_admin_of_another_group_cannot_hand_over(path, group):
    anna, ben, carl, friends = group
    auth.create_group(carl, "Others", path=path)
    with pytest.raises(AuthError, match="Group not found"):
        auth.hand_over_admin(carl, friends.id, carl, NOW, path)
    assert stored_admin_id(path, friends.id) == anna


@pytest.mark.parametrize("target", ["carl", "ghost"])
def test_new_admin_must_be_a_member_of_this_group(path, group, target):
    anna, ben, carl, friends = group
    new_admin = carl if target == "carl" else "ghost"
    with pytest.raises(AuthError, match="must be a member"):
        auth.hand_over_admin(anna, friends.id, new_admin, NOW, path)
    assert roles(path, friends.id) == {anna: "admin", ben: "member"}
    assert storage.load_data(path)["notifications"] == []


def test_admin_cannot_hand_over_to_themselves(path, group):
    anna, ben, _, friends = group
    with pytest.raises(AuthError, match="already the admin"):
        auth.hand_over_admin(anna, friends.id, anna, NOW, path)
    assert storage.load_data(path)["notifications"] == []


def test_hand_over_in_one_group_leaves_others_alone(path, group):
    anna, ben, _, friends = group
    work = auth.create_group(anna, "Work", path=path)
    auth.join_group(ben, work.invite_code, path)
    auth.hand_over_admin(anna, friends.id, ben, NOW, path)
    assert roles(path, work.id) == {anna: "admin", ben: "member"}
    assert stored_admin_id(path, work.id) == anna

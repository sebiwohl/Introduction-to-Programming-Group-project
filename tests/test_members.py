"""Tests for src/members.py and the restriction check in auth.require_permission."""

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



def is_restricted(path, user_id, group_id):
    """Return the stored restricted flag of the user's membership in the group."""
    return next(m["restricted"] for m in storage.load_data(path)["memberships"]
                if m["user_id"] == user_id and m["group_id"] == group_id)


# --- the permission check -------------------------------------------------------

@pytest.mark.parametrize("action", sorted(auth.MEMBER_ACTIONS - {auth.VIEW_FEED}))
def test_restricted_member_can_only_view(action):
    user = User("u1", "anna", "h", group_id="g1", restricted=True)
    auth.require_permission(user, auth.VIEW_FEED)
    with pytest.raises(AuthError, match="restricted"):
        auth.require_permission(user, action)


def test_restricted_creator_cannot_resolve_own_event(path, group):
    anna, ben, _, friends = group
    event = service.create_event(ben, "Rain tomorrow", CLOSE, NOW, path=path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        service.resolve_event(ben, event.id, "yes", CLOSE + timedelta(hours=1), path)


def test_restriction_is_loaded_from_the_membership(path, group):
    anna, ben, _, friends = group
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    data = storage.load_data(path)
    assert auth.user_in_group(data, ben, friends.id).restricted is True
    assert auth.user_in_group(data, anna, friends.id).restricted is False


# --- restricting a member -------------------------------------------------------

def test_restricted_member_cannot_bet_or_create_but_can_view(path, group):
    anna, ben, _, friends = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        service.place_bet(ben, event.id, "yes", Decimal("10"), NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        service.create_event(ben, "Snow", CLOSE, NOW, path=path)
    assert [e.event.id for e in service.page_data(ben, NOW, path).events] == [event.id]


def test_restricting_keeps_balance_and_open_bets(path, group):
    anna, ben, _, friends = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ben, event.id, "yes", Decimal("40"), NOW, path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    data = storage.load_data(path)
    assert auth.user_in_group(data, ben, friends.id).balance == Decimal("460.00")
    assert len(data["bets"]) == 1


def test_lifting_the_restriction_allows_betting_again(path, group):
    anna, ben, _, friends = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    members.set_restricted(anna, friends.id, ben, False, NOW, path)
    service.place_bet(ben, event.id, "yes", Decimal("10"), NOW, path)
    assert not is_restricted(path, ben, friends.id)


def test_member_is_notified_of_restrict_and_lift(path, group):
    anna, ben, _, friends = group
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    members.set_restricted(anna, friends.id, ben, False, NOW + timedelta(minutes=1), path)
    texts = [n.text for n in notifications.notifications_for(ben, path)]
    assert texts == ["You can bet in Friends again.",
                     "The admin restricted you in Friends: you can only view for now."]


def test_setting_the_same_state_again_changes_nothing(path, group):
    anna, ben, _, friends = group
    members.set_restricted(anna, friends.id, ben, False, NOW, path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    assert len(notifications.notifications_for(ben, path)) == 1


def test_member_cannot_restrict(path, group):
    anna, ben, _, friends = group
    with pytest.raises(AuthError, match="Only the group admin"):
        members.set_restricted(ben, friends.id, anna, True, NOW, path)
    assert not is_restricted(path, anna, friends.id)


def test_admin_cannot_restrict_themselves(path, group):
    anna, _, _, friends = group
    with pytest.raises(AuthError, match="cannot restrict themselves"):
        members.set_restricted(anna, friends.id, anna, True, NOW, path)


def test_cannot_restrict_someone_outside_the_group(path, group):
    anna, _, carl, friends = group
    with pytest.raises(AuthError, match="not a member"):
        members.set_restricted(anna, friends.id, carl, True, NOW, path)
    assert storage.load_data(path)["notifications"] == []


def test_outsider_or_other_admin_cannot_restrict(path, group):
    anna, ben, carl, friends = group
    auth.create_group(carl, "Others", path=path)
    with pytest.raises(AuthError, match="Group not found"):
        members.set_restricted(carl, friends.id, ben, True, NOW, path)
    assert not is_restricted(path, ben, friends.id)


def test_restriction_applies_only_in_that_group(path, group):
    anna, ben, _, friends = group
    work = auth.create_group(ben, "Work", path=path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    assert not is_restricted(path, ben, work.id)


@pytest.mark.parametrize("value", [None, "yes", 1])
def test_restricted_value_must_be_true_or_false(path, group, value):
    anna, ben, _, friends = group
    with pytest.raises(AuthError, match="restrict or lift"):
        members.set_restricted(anna, friends.id, ben, value, NOW, path)


# --- deleting the group ---------------------------------------------------------

def test_last_member_admin_deletes_group_and_everything_in_it(path, group):
    anna, ben, _, friends = group
    event = service.create_event(anna, "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ben, event.id, "yes", Decimal("10"), NOW, path)
    members.set_restricted(anna, friends.id, ben, True, NOW, path)
    service.resolve_event(anna, event.id, "yes", CLOSE + timedelta(hours=1), path)  # refund
    members.leave_group(ben, friends.id, CLOSE + timedelta(hours=1), path)
    members.delete_group(anna, friends.id, path)
    data = storage.load_data(path)
    for section in ("groups", "memberships", "events", "bets", "polls", "topups",
                    "repayments", "notifications"):
        assert data[section] == [], section
    assert len(data["users"]) == 3


def test_deleting_one_group_keeps_the_others(path, group):
    anna, ben, carl, friends = group
    others = auth.create_group(carl, "Others", path=path)
    auth.join_group(ben, others.invite_code, path)
    balances.top_up(carl, others.id, 100, NOW, path)
    kept = service.create_event(carl, "Kept event", CLOSE, NOW, path=path)
    solo = auth.create_group(anna, "Solo", path=path)
    members.delete_group(anna, solo.id, path)
    data = storage.load_data(path)
    assert {g["id"] for g in data["groups"]} == {friends.id, others.id}
    assert [e["id"] for e in data["events"]] == [kept.id]


def test_cannot_delete_group_with_other_members(path, group):
    anna, _, _, friends = group
    with pytest.raises(AuthError, match="Only the last member"):
        members.delete_group(anna, friends.id, path)
    assert len(storage.load_data(path)["groups"]) == 1


def test_member_cannot_delete_group(path, group):
    anna, ben, _, friends = group
    with pytest.raises(AuthError, match="Only the group admin"):
        members.delete_group(ben, friends.id, path)


def test_outsider_cannot_delete_group(path, group):
    _, _, carl, friends = group
    with pytest.raises(AuthError, match="Group not found"):
        members.delete_group(carl, friends.id, path)
    with pytest.raises(AuthError, match="Group not found"):
        members.delete_group(carl, "no-such-group", path)

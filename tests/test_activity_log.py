"""Tests for the group activity log in the bell: who gets which entry.

Every member of the group gets an entry, except whoever did the action.
Members of other groups get nothing.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, moderation, notifications, service, storage

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
    """anna (admin), ben, carl, dan in Friends, topped up 100; eve is outside.

    Returns (ids, group id, invite code). The log is emptied after the setup.
    """
    names = ("anna", "ben", "carl", "dan", "eve")
    ids = {name: auth.sign_up(name, PASSWORD, path).id for name in names}
    friends = auth.create_group(ids["anna"], "Friends", path=path)
    for name in ("ben", "carl", "dan"):
        auth.join_group(ids[name], friends.invite_code, path, now=NOW)
    balances.top_up(ids["anna"], friends.id, 100, NOW, path)
    with storage.transaction(path) as data:
        data["notifications"] = []
    return ids, friends.id, friends.invite_code


def log(path, user_id):
    """Return the texts of the user's activity log entries, oldest first."""
    entries = [n for n in notifications.notifications_for(user_id, path)
               if notifications.is_activity(n)]
    return [n.text for n in reversed(entries)]


def logs(path, ids, *names):
    """Return {name: log texts} for the named users."""
    return {name: log(path, ids[name]) for name in names}


def event(path, ids, title="Rain tomorrow"):
    """Create an open event by ben and return its id."""
    return service.create_event(ids["ben"], title, CLOSE, NOW, path=path).id


# --- members and top-ups ----------------------------------------------------------

def test_join_is_logged_for_the_others(path, group):
    ids, group_id, code = group
    auth.join_group(ids["eve"], code, path, now=NOW)
    assert logs(path, ids, "anna", "ben", "eve") == {
        "anna": ["eve joined Friends."], "ben": ["eve joined Friends."], "eve": []}


def test_top_up_is_logged_for_everyone_but_the_admin(path, group):
    ids, group_id, _ = group
    balances.top_up(ids["anna"], group_id, Decimal("25.50"), NOW, path)
    assert logs(path, ids, "anna", "ben", "eve") == {
        "anna": [], "ben": ["anna topped up everyone with 25.50 coins."], "eve": []}


def test_leaving_is_logged_for_the_remaining_members(path, group):
    ids, group_id, _ = group
    members.leave_group(ids["dan"], group_id, NOW, path)
    assert logs(path, ids, "anna", "carl", "dan") == {
        "anna": ["dan left Friends."], "carl": ["dan left Friends."], "dan": []}


def test_removal_is_logged_for_the_others(path, group):
    ids, group_id, _ = group
    members.remove_member(ids["anna"], group_id, ids["dan"], NOW, path)
    assert logs(path, ids, "anna", "ben", "dan") == {
        "anna": [], "ben": ["anna removed dan from Friends."], "dan": []}


def test_hand_over_is_logged_for_the_others(path, group):
    ids, group_id, _ = group
    auth.hand_over_admin(ids["anna"], group_id, ids["ben"], NOW, path)
    assert logs(path, ids, "anna", "ben", "carl") == {
        "anna": [], "ben": [], "carl": ["anna handed the admin role to ben."]}


def test_refused_actions_log_nothing(path, group):
    ids, group_id, _ = group
    for action in (lambda: balances.top_up(ids["ben"], group_id, 10, NOW, path),
                   lambda: members.remove_member(ids["ben"], group_id, ids["dan"], NOW, path),
                   lambda: auth.hand_over_admin(ids["ben"], group_id, ids["carl"], NOW, path)):
        with pytest.raises(auth.AuthError):
            action()
    assert storage.load_data(path)["notifications"] == []


# --- flags, polls and deleting ------------------------------------------------------

def test_flag_is_logged_for_the_others(path, group):
    ids, _, _ = group
    event_id = event(path, ids)
    moderation.flag_event(ids["carl"], event_id, NOW, path)
    assert logs(path, ids, "anna", "carl", "eve") == {
        "anna": ["carl flagged “Rain tomorrow”."], "carl": [], "eve": []}


def test_poll_opened_by_flags_is_logged_for_everyone(path, group):
    ids, _, _ = group
    event_id = event(path, ids)
    moderation.flag_event(ids["carl"], event_id, NOW, path)
    moderation.flag_event(ids["dan"], event_id, NOW, path)  # 2 of 4: poll
    paused = "Enough flags on “Rain tomorrow”: betting is paused. Vote in the poll on the Feed."
    # the last flagger gets it too, as they still have to vote (same time: compare as sets)
    assert set(log(path, ids["dan"])) == {"carl flagged “Rain tomorrow”.", paused}
    # the flag that opens the poll is reported by the poll entry, not a second flag entry
    assert set(log(path, ids["anna"])) == {"carl flagged “Rain tomorrow”.", paused}


def test_deletion_by_flags_is_logged(path, group):
    ids, group_id, _ = group
    members.set_restricted(ids["anna"], group_id, ids["dan"], True, NOW, path)  # 3 count
    event_id = event(path, ids)
    moderation.flag_event(ids["carl"], event_id, NOW, path)
    moderation.flag_event(ids["ben"], event_id, NOW, path)  # 2 of 3: deleted
    deleted = ("“Rain tomorrow” was deleted: more than half of the members flagged it. "
               "Every stake was refunded.")
    assert deleted in log(path, ids["anna"])
    assert deleted not in log(path, ids["ben"])  # ben flagged last: he saw the message


def test_admin_poll_and_its_result_are_logged(path, group):
    ids, _, _ = group
    event_id = event(path, ids)
    moderation.open_poll(ids["anna"], event_id, NOW, path)
    for name in ("ben", "carl"):
        moderation.vote(ids[name], event_id, "delete", NOW, path)
    moderation.vote(ids["dan"], event_id, "keep", NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    assert log(path, ids["ben"]) == [
        "anna opened a poll on “Rain tomorrow”: betting is paused. Vote on the Feed.",
        "The poll on “Rain tomorrow” ended: deleted (2 of 3 votes for delete). "
        "Every stake was refunded."]
    assert log(path, ids["anna"]) == [log(path, ids["ben"])[1]]


def test_kept_poll_is_logged(path, group):
    ids, _, _ = group
    event_id = event(path, ids)
    moderation.open_poll(ids["anna"], event_id, NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    assert log(path, ids["carl"])[-1] == ("The poll on “Rain tomorrow” ended: kept "
                                          "(0 of 0 votes for delete). Betting is open again.")


def test_deleting_an_event_is_logged_for_the_others(path, group):
    ids, _, _ = group
    event_id = event(path, ids)
    moderation.delete_event(ids["ben"], event_id, NOW, path)
    assert logs(path, ids, "ben", "carl") == {
        "ben": [], "carl": ["ben deleted “Rain tomorrow”. Every stake was refunded."]}


def test_long_titles_are_shortened_so_the_entry_fits(path, group):
    ids, _, _ = group
    event_id = event(path, ids, title="x" * 120)
    moderation.flag_event(ids["carl"], event_id, NOW, path)
    [entry] = log(path, ids["anna"])
    assert entry == f"carl flagged “{'x' * 59}…”."


def test_activity_entries_are_told_apart_from_personal_ones(path, group):
    ids, group_id, _ = group
    auth.hand_over_admin(ids["anna"], group_id, ids["ben"], NOW, path)
    kinds = {name: {n.text: notifications.is_activity(n)
                    for n in notifications.notifications_for(ids[name], path)}
             for name in ("ben", "carl")}
    assert kinds == {"ben": {"You are now the admin of Friends.": False},
                     "carl": {"anna handed the admin role to ben.": True}}

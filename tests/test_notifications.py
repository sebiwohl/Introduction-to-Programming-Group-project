"""Tests for src/notifications.py: adding, listing and marking notifications as read."""

from datetime import datetime, timedelta, timezone

import pytest

from src import auth, notifications, storage
from src.betting import BettingError
from src.notifications import NotificationError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Use few hash iterations so the tests run fast (real code uses 600,000)."""
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 1_000)
    monkeypatch.setattr(auth, "_dummy_hash", None)


@pytest.fixture
def setup(tmp_path):
    """Return (path, anna id, ben id, group id) for a group with two members."""
    path = tmp_path / "data.json"
    anna = auth.sign_up("anna", PASSWORD, path).id
    ben = auth.sign_up("ben", PASSWORD, path).id
    group = auth.create_group(anna, "Friends", path=path)
    auth.join_group(ben, group.invite_code, path)
    clear_notifications(path)  # the group's activity log noted the join
    return path, anna, ben, group.id


def clear_notifications(path):
    """Start a test with no notifications (setting up the group logs joins and top-ups)."""
    with storage.transaction(path) as data:
        data["notifications"] = []


def add(path, user_id, group_id, text, now=NOW):
    """Add one notification in its own transaction and return it."""
    with storage.transaction(path) as data:
        return notifications.add_notification(data, user_id, group_id, text, now)


def test_added_notification_is_saved_unread_and_trimmed(setup):
    path, anna, _, group = setup
    note = add(path, anna, group, "  You are the new admin.  ")
    [saved] = notifications.notifications_for(anna, path)
    assert saved == note
    assert (saved.text, saved.read, saved.group_id) == ("You are the new admin.", False, group)


def test_notification_without_group_is_allowed(setup):
    path, anna, _, _ = setup
    add(path, anna, None, "Welcome!")
    assert notifications.notifications_for(anna, path)[0].group_id is None


@pytest.mark.parametrize("text", ["", "   ", None, "x" * (notifications.MAX_TEXT_LENGTH + 1)])
def test_bad_text_is_rejected_and_nothing_saved(setup, text):
    path, anna, _, group = setup
    with pytest.raises(NotificationError):
        add(path, anna, group, text)
    assert storage.load_data(path)["notifications"] == []


def test_text_of_maximum_length_is_accepted(setup):
    path, anna, _, group = setup
    add(path, anna, group, "x" * notifications.MAX_TEXT_LENGTH)
    assert len(notifications.notifications_for(anna, path)) == 1


@pytest.mark.parametrize("which", ["user", "group"])
def test_unknown_user_or_group_is_rejected(setup, which):
    path, anna, _, group = setup
    user_id, group_id = ("nobody", group) if which == "user" else (anna, "nogroup")
    with pytest.raises(NotificationError):
        add(path, user_id, group_id, "Hello")
    assert storage.load_data(path)["notifications"] == []


def test_error_is_a_betting_error_so_the_ui_catches_one_type():
    assert issubclass(NotificationError, BettingError)


def test_list_shows_only_own_notifications_newest_first(setup):
    path, anna, ben, group = setup
    old = add(path, anna, group, "Old", NOW - timedelta(hours=1))
    new = add(path, anna, group, "New", NOW)
    add(path, ben, group, "For Ben")
    assert [n.id for n in notifications.notifications_for(anna, path)] == [new.id, old.id]
    assert [n.text for n in notifications.notifications_for(ben, path)] == ["For Ben"]


def test_unread_count(setup):
    path, anna, _, group = setup
    first = add(path, anna, group, "One")
    add(path, anna, group, "Two")
    notifications.mark_read(anna, first.id, path)
    assert notifications.unread_count(notifications.notifications_for(anna, path)) == 1
    assert notifications.unread_count([]) == 0


def test_mark_read_marks_only_that_notification(setup):
    path, anna, _, group = setup
    first = add(path, anna, group, "One")
    add(path, anna, group, "Two")
    notifications.mark_read(anna, first.id, path)
    read = {n.text: n.read for n in notifications.notifications_for(anna, path)}
    assert read == {"One": True, "Two": False}


def test_marking_twice_is_harmless(setup):
    path, anna, _, group = setup
    note = add(path, anna, group, "One")
    notifications.mark_read(anna, note.id, path)
    notifications.mark_read(anna, note.id, path)
    assert notifications.notifications_for(anna, path)[0].read


def test_cannot_mark_someone_elses_notification(setup):
    path, anna, ben, group = setup
    note = add(path, anna, group, "Private")
    with pytest.raises(NotificationError, match="Unknown notification"):
        notifications.mark_read(ben, note.id, path)
    assert not notifications.notifications_for(anna, path)[0].read


def test_unknown_notification_gives_the_same_error(setup):
    path, anna, _, _ = setup
    with pytest.raises(NotificationError, match="Unknown notification"):
        notifications.mark_read(anna, "missing", path)


def test_mark_all_read_changes_only_own_unread(setup):
    path, anna, ben, group = setup
    first = add(path, anna, group, "One")
    add(path, anna, group, "Two")
    add(path, ben, group, "For Ben")
    notifications.mark_read(anna, first.id, path)
    assert notifications.mark_all_read(anna, path) == 1
    assert notifications.unread_count(notifications.notifications_for(anna, path)) == 0
    assert notifications.unread_count(notifications.notifications_for(ben, path)) == 1


def test_mark_all_read_with_nothing_unread_returns_zero(setup):
    path, anna, _, _ = setup
    assert notifications.mark_all_read(anna, path) == 0


# --- the bell: page data and "Mark all as read" ------------------------------------

def test_page_data_carries_own_notifications_newest_first(setup):
    from src import service
    path, anna, ben, group = setup
    old = add(path, anna, group, "Old", NOW - timedelta(hours=1))
    new = add(path, anna, group, "New", NOW)
    add(path, ben, group, "For Ben")
    page = service.page_data(anna, NOW, path)
    assert [n.id for n in page.notifications] == [new.id, old.id]


def test_mark_all_read_can_leave_unseen_wins_for_the_confetti(setup):
    from src import service
    path, anna, _, group = setup
    add(path, anna, group, "Plain")
    with storage.transaction(path) as data:
        data["notifications"].append({"id": f"{service.WIN_PREFIX}e1:{anna}", "user_id": anna,
                                      "group_id": group, "text": "You called it",
                                      "created_at": NOW.isoformat(), "read": False})
    assert notifications.mark_all_read(anna, path, skip_prefix=service.WIN_PREFIX) == 1
    unread = [n.id for n in notifications.notifications_for(anna, path) if not n.read]
    assert unread == [f"{service.WIN_PREFIX}e1:{anna}"]

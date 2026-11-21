"""Tests for the optional note a member leaves when flagging an event."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, convert, moderation, notifications, service, storage
from src.auth import AuthError
from src.models import MAX_FLAG_NOTE_LENGTH

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
def four(path):
    """Return (ids, event id): 4 members of one group; user1 created an open event."""
    ids = [auth.sign_up(f"user{i}", PASSWORD, path).id for i in range(4)]
    group = auth.create_group(ids[0], "Friends", path=path)
    for user_id in ids[1:]:
        auth.join_group(user_id, group.invite_code, path)
    balances.top_up(ids[0], group.id, Decimal("500"), NOW, path)
    event = service.create_event(ids[1], "Rain tomorrow", CLOSE, NOW, path=path)
    return ids, event.id


def stored_event(path, event_id):
    """Return the stored event dict."""
    return next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)


def feed_item(path, user_id, event_id):
    """Return the feed summary of one event as a member sees it."""
    return next(s for s in service.page_data(user_id, NOW, path).events if s.event.id == event_id)


# --- flagging with a note ------------------------------------------------------------

def test_flag_note_is_saved_and_shown_to_the_group(path, four):
    """The note is stored with the flag and the feed shows it with the flagger's name."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="The creator already knows")
    assert stored_event(path, event_id)["flag_notes"] == {ids[2]: "The creator already knows"}
    assert feed_item(path, ids[3], event_id).flag_notes == [("user2", "The creator already knows")]


def test_flag_note_is_cleaned(path, four):
    """Spaces and line breaks are trimmed and squeezed to single spaces."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="  Already\n\n  happened   ")
    assert stored_event(path, event_id)["flag_notes"] == {ids[2]: "Already happened"}


def test_flag_without_note_stores_no_notes(path, four):
    """A flag without a note (or only spaces) keeps the event exactly as before notes existed."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="   ")
    event = stored_event(path, event_id)
    assert event["flags"] == [ids[2]] and "flag_notes" not in event
    assert feed_item(path, ids[3], event_id).flag_notes == []


def test_too_long_flag_note_is_rejected_and_nothing_changes(path, four):
    """A note over the limit is refused, and the flag is not recorded either."""
    ids, event_id = four
    with pytest.raises(AuthError, match=str(MAX_FLAG_NOTE_LENGTH)):
        moderation.flag_event(ids[2], event_id, NOW, path, note="x" * (MAX_FLAG_NOTE_LENGTH + 1))
    event = stored_event(path, event_id)
    assert event["flags"] == [] and "flag_notes" not in event


def test_note_at_the_limit_is_accepted(path, four):
    """A note of exactly the maximum length is fine."""
    ids, event_id = four
    note = "x" * MAX_FLAG_NOTE_LENGTH
    moderation.flag_event(ids[2], event_id, NOW, path, note=note)
    assert stored_event(path, event_id)["flag_notes"][ids[2]] == note


def test_second_flag_with_note_still_rejected(path, four):
    """A note does not get around the one-flag-per-member rule."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="first")
    with pytest.raises(AuthError):
        moderation.flag_event(ids[2], event_id, NOW, path, note="second")
    assert stored_event(path, event_id)["flag_notes"] == {ids[2]: "first"}


def test_activity_log_includes_the_note(path, four):
    """The other members see the reason in their notifications too."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="Already happened")
    texts = [n.text for n in notifications.notifications_for(ids[3], path)]
    assert any("flagged" in t and "Already happened" in t for t in texts)


def test_long_note_still_fits_in_the_activity_log(path, four):
    """A long name, title and note never break the notification length limit."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="y" * MAX_FLAG_NOTE_LENGTH)
    texts = [n.text for n in notifications.notifications_for(ids[3], path)]
    assert all(len(t) <= notifications.MAX_TEXT_LENGTH for t in texts)


def test_notes_are_cleared_with_the_flags_when_a_poll_keeps_the_event(path, four):
    """When the group keeps the event, flags and their notes start again from nothing."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path, note="Fishy")
    moderation.open_poll(ids[0], event_id, NOW, path)
    moderation.vote(ids[3], event_id, "keep", NOW, path)
    moderation.vote(ids[1], event_id, "keep", NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    event = stored_event(path, event_id)
    assert (event["status"], event["flags"]) == ("open", []) and "flag_notes" not in event


# --- stored format -------------------------------------------------------------------

def test_event_round_trip_keeps_the_notes():
    """Converting an event to a dict and back keeps its flag notes."""
    data = {"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "T", "description": "",
            "close_time": "2026-11-22T12:00:00+00:00", "status": "open", "outcome": None,
            "flags": ["u2"], "flag_notes": {"u2": "Why"}, "type": "yes_no", "line": None,
            "event_time": "2026-11-22T12:00:00+00:00", "result_value": None}
    event = convert.event_from_dict(data)
    assert event.flag_notes == {"u2": "Why"}
    assert convert.event_to_dict(event)["flag_notes"] == {"u2": "Why"}


def test_event_without_notes_is_stored_without_the_field():
    """Old data has no flag_notes; an event without notes is written the same way."""
    data = {"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "T", "description": "",
            "close_time": "2026-11-22T12:00:00+00:00", "status": "open", "outcome": None,
            "flags": [], "type": "yes_no", "line": None,
            "event_time": "2026-11-22T12:00:00+00:00", "result_value": None}
    assert "flag_notes" not in convert.event_to_dict(convert.event_from_dict(data))


def corrupt(path, event_id, notes):
    """Write the data file again with the event's flag_notes replaced by `notes`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for event in data["events"]:
        if event["id"] == event_id:
            event["flag_notes"] = notes
    path.write_text(json.dumps(data), encoding="utf-8")


def test_storage_rejects_a_note_from_someone_who_did_not_flag(path, four):
    """A note without a matching flag is invalid data and is reported, not loaded."""
    ids, event_id = four
    corrupt(path, event_id, {ids[3]: "I never flagged"})
    with pytest.raises(storage.StorageError, match="flag_notes"):
        storage.load_data(path)


@pytest.mark.parametrize("bad", ["just text", {"nobody": "x"}, "LONG", "EMPTY", "SPACES"])
def test_storage_rejects_badly_formed_notes(path, four, bad):
    """Notes must be a map of existing user ids to trimmed, non-empty notes within the limit."""
    ids, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path)
    notes = {"LONG": {ids[2]: "x" * (MAX_FLAG_NOTE_LENGTH + 1)}, "EMPTY": {ids[2]: ""},
             "SPACES": {ids[2]: " padded "}}.get(bad, bad) if isinstance(bad, str) else bad
    corrupt(path, event_id, notes)
    with pytest.raises(storage.StorageError, match="flag_notes"):
        storage.load_data(path)

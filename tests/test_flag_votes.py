"""Tests for flags counting as "delete" votes in polls, and switching that vote to "keep" once."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, moderation, service, storage
from src.auth import AuthError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLOSE = NOW + timedelta(days=2)
LATER = NOW + timedelta(minutes=10)
END = NOW + timedelta(hours=1)
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


def make_group(path, size):
    """Create a group of `size` members (u0 is the admin) with an event by u1.

    Returns (ids, group id, event id).
    """
    ids = [auth.sign_up(f"user{i}", PASSWORD, path).id for i in range(size)]
    group = auth.create_group(ids[0], "Friends", path=path)
    for user_id in ids[1:]:
        auth.join_group(user_id, group.invite_code, path)
    balances.top_up(ids[0], group.id, Decimal("100"), NOW, path)
    event = service.create_event(ids[1], "Rain tomorrow", CLOSE, NOW, path=path)
    return ids, group.id, event.id


@pytest.fixture
def eight(path):
    """Return (ids, group id, event id) for a group of 8 members."""
    return make_group(path, 8)


def poll_of(path, event_id):
    """Return the event's newest stored poll."""
    return [p for p in storage.load_data(path)["polls"] if p["event_id"] == event_id][-1]


def end_poll(path, event_id):
    """End the poll via refresh() and return (result, event status)."""
    service.refresh(END, path)
    event = next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)
    return poll_of(path, event_id)["result"], event["status"]


# --- flags become "delete" votes ------------------------------------------------------

def test_auto_poll_starts_with_a_delete_vote_per_flag(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    assert moderation.flag_event(ids[3], event_id, NOW, path) == moderation.POLL_OPENED
    assert poll_of(path, event_id)["votes"] == {ids[2]: "delete", ids[3]: "delete"}


def test_admin_poll_counts_earlier_flags(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[4], event_id, NOW, path)
    moderation.open_poll(ids[0], event_id, NOW, path)
    assert poll_of(path, event_id)["votes"] == {ids[4]: "delete"}


def test_admin_poll_without_flags_starts_empty(path, eight):
    ids, _, event_id = eight
    moderation.open_poll(ids[0], event_id, NOW, path)
    assert poll_of(path, event_id)["votes"] == {}


def test_flags_of_restricted_members_give_no_vote(path, eight):
    ids, group_id, event_id = eight
    moderation.flag_event(ids[5], event_id, NOW, path)
    members.set_restricted(ids[0], group_id, ids[5], True, NOW, path)
    moderation.open_poll(ids[0], event_id, NOW, path)
    assert poll_of(path, event_id)["votes"] == {}


def test_flag_votes_alone_delete_the_event_when_nobody_else_votes(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    assert end_poll(path, event_id) == ("deleted", "deleted")


def test_enough_keep_votes_still_keep_the_event(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    for voter in ids[4:6]:
        moderation.vote(voter, event_id, "keep", LATER, path)  # 2 delete, 2 keep: a tie
    assert end_poll(path, event_id) == ("kept", "open")


# --- switching the flag vote to "keep" once --------------------------------------------

def test_flagger_may_switch_to_keep(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    moderation.vote(ids[2], event_id, "keep", LATER, path)
    assert poll_of(path, event_id)["votes"] == {ids[2]: "keep", ids[3]: "delete"}


def test_flagger_cannot_switch_twice(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    moderation.vote(ids[2], event_id, "keep", LATER, path)
    with pytest.raises(AuthError, match="already voted"):
        moderation.vote(ids[2], event_id, "delete", LATER, path)
    assert poll_of(path, event_id)["votes"][ids[2]] == "keep"


def test_flagger_cannot_vote_delete_a_second_time(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    with pytest.raises(AuthError, match="already voted"):
        moderation.vote(ids[2], event_id, "delete", LATER, path)


def test_non_flagger_still_votes_only_once(path, eight):
    ids, _, event_id = eight
    moderation.open_poll(ids[0], event_id, NOW, path)
    moderation.vote(ids[4], event_id, "delete", LATER, path)
    with pytest.raises(AuthError, match="already voted"):
        moderation.vote(ids[4], event_id, "keep", LATER, path)
    assert poll_of(path, event_id)["votes"] == {ids[4]: "delete"}


def test_vote_from_flag_helper():
    assert moderation.vote_from_flag({"a": "delete"}, ["a"], "a")
    assert not moderation.vote_from_flag({"a": "keep"}, ["a"], "a")  # already switched
    assert not moderation.vote_from_flag({"a": "delete"}, [], "a")  # voted, did not flag
    assert not moderation.vote_from_flag({}, ["a"], "a")  # flagged, no vote (restricted)


def test_votes_survive_storage_validation(path, eight):
    ids, _, event_id = eight
    moderation.flag_event(ids[2], event_id, NOW, path)
    moderation.flag_event(ids[3], event_id, NOW, path)
    storage.validate(storage.load_data(path))


def test_poll_without_flags_and_without_votes_keeps_the_event(path, eight):
    ids, _, event_id = eight
    moderation.open_poll(ids[0], event_id, NOW, path)
    assert end_poll(path, event_id) == ("kept", "open")

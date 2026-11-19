"""Tests for src/moderation.py: deleting events, flags and polls."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, moderation, service, storage
from src.auth import AuthError
from src.models import Event, User

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLOSE = NOW + timedelta(days=2)
PASSWORD = "correct horse"
START = Decimal("500")


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
    """Create a group of `size` members (u0 is the admin); return (ids, group id)."""
    ids = [auth.sign_up(f"user{i}", PASSWORD, path).id for i in range(size)]
    group = auth.create_group(ids[0], "Friends", path=path)
    for user_id in ids[1:]:
        auth.join_group(user_id, group.invite_code, path)
    balances.top_up(ids[0], group.id, START, NOW, path)
    return ids, group.id


@pytest.fixture
def four(path):
    """Return (ids, group id, event id): 4 members; user1 created an event, two bets on it."""
    ids, group_id = make_group(path, 4)
    event = service.create_event(ids[1], "Rain tomorrow", CLOSE, NOW, path=path)
    service.place_bet(ids[2], event.id, "yes", Decimal("40"), NOW, path)
    service.place_bet(ids[3], event.id, "no", Decimal("60"), NOW, path)
    return ids, group_id, event.id


def stored_event(path, event_id):
    """Return the stored event dict."""
    return next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)


def stored_polls(path, event_id):
    """Return the stored polls of the event."""
    return [p for p in storage.load_data(path)["polls"] if p["event_id"] == event_id]


def balance(path, user_id, group_id):
    """Return the user's balance in the group."""
    return auth.user_in_group(storage.load_data(path), user_id, group_id).balance


def assert_refunded(path, ids, group_id, event_id):
    """Check the event is deleted and every member is back at the start balance."""
    assert stored_event(path, event_id)["status"] == "deleted"
    assert all(balance(path, u, group_id) == START for u in ids)
    bets = [b for b in storage.load_data(path)["bets"] if b["event_id"] == event_id]
    assert bets and all(b["payout"] == b["stake"] for b in bets)


# --- permissions ----------------------------------------------------------------

def test_creator_and_admin_may_delete_but_not_other_members():
    event = Event(id="e1", group_id="g1", creator_id="u2", title="T", close_time=CLOSE)
    auth.require_permission(User("u1", "admin", "h", group_id="g1", role="admin"),
                            auth.DELETE_EVENT, event)
    auth.require_permission(User("u2", "creator", "h", group_id="g1"), auth.DELETE_EVENT, event)
    with pytest.raises(AuthError, match="Only the creator or the group admin can delete"):
        auth.require_permission(User("u3", "other", "h", group_id="g1"),
                                auth.DELETE_EVENT, event)
    with pytest.raises(AuthError, match="Needs an event"):
        auth.require_permission(User("u2", "creator", "h", group_id="g1"), auth.DELETE_EVENT)


# --- deleting an event ----------------------------------------------------------

@pytest.mark.parametrize("who", [0, 1])  # admin, creator
def test_creator_or_admin_deletes_and_everyone_is_refunded(path, four, who):
    ids, group_id, event_id = four
    moderation.delete_event(ids[who], event_id, NOW, path)
    assert_refunded(path, ids, group_id, event_id)


def test_other_member_cannot_delete(path, four):
    ids, _, event_id = four
    with pytest.raises(AuthError, match="Only the creator or the group admin"):
        moderation.delete_event(ids[2], event_id, NOW, path)
    assert stored_event(path, event_id)["status"] == "open"


def test_delete_only_more_than_3_hours_before_close(path, four):
    ids, group_id, event_id = four
    exactly_3h = CLOSE - timedelta(hours=3)
    with pytest.raises(AuthError, match="more than 3 hours"):
        moderation.delete_event(ids[0], event_id, exactly_3h, path)
    moderation.delete_event(ids[0], event_id, exactly_3h - timedelta(seconds=1), path)
    assert_refunded(path, ids, group_id, event_id)


@pytest.mark.parametrize("status", ["closed", "resolved", "deleted"])
def test_cannot_delete_closed_resolved_or_deleted(path, four, status):
    ids, _, event_id = four
    with storage.transaction(path) as data:
        event = next(e for e in data["events"] if e["id"] == event_id)
        event["status"] = status
        if status in ("resolved", "deleted"):
            event["outcome"] = "yes" if status == "resolved" else None
            for bet in data["bets"]:
                bet["payout"] = bet["stake"]
    with pytest.raises(AuthError, match="cannot be deleted"):
        moderation.delete_event(ids[0], event_id, NOW, path)


def test_outsider_cannot_delete_and_learns_nothing(path, four):
    ids, _, event_id = four
    outsider = auth.sign_up("outsider", PASSWORD, path).id
    auth.create_group(outsider, "Others", path=path)
    for event in (event_id, "no-such-event"):
        with pytest.raises(AuthError, match="Event not found"):
            moderation.delete_event(outsider, event, NOW, path)


def test_restricted_creator_cannot_delete(path, four):
    ids, group_id, event_id = four
    members.set_restricted(ids[0], group_id, ids[1], True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        moderation.delete_event(ids[1], event_id, NOW, path)


def test_deleting_a_paused_event_ends_its_poll(path, four):
    ids, group_id, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    moderation.delete_event(ids[0], event_id, NOW, path)
    assert [p["result"] for p in stored_polls(path, event_id)] == ["deleted"]
    assert_refunded(path, ids, group_id, event_id)


# --- flags ----------------------------------------------------------------------

@pytest.mark.parametrize("members_count, expected", [(1, 2), (4, 2), (8, 2), (9, 3), (12, 3)])
def test_poll_threshold_is_25_percent_at_least_2(members_count, expected):
    assert moderation.poll_threshold(members_count) == expected


def test_one_flag_in_four_only_records_it(path, four):
    ids, _, event_id = four
    assert moderation.flag_event(ids[2], event_id, NOW, path) == moderation.FLAGGED
    event = stored_event(path, event_id)
    assert (event["flags"], event["status"]) == ([ids[2]], "open")


def test_two_flags_in_four_open_a_poll_and_pause_betting(path, four):
    ids, _, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path)
    assert moderation.flag_event(ids[3], event_id, NOW, path) == moderation.POLL_OPENED
    assert stored_event(path, event_id)["status"] == "paused"
    [poll] = stored_polls(path, event_id)
    assert poll["opened_by"] == "auto" and poll["result"] is None
    with pytest.raises(Exception, match="not open"):
        service.place_bet(ids[0], event_id, "yes", Decimal("5"), NOW, path)


def test_more_than_half_flags_delete_without_poll(path):
    ids, group_id = make_group(path, 3)
    event = service.create_event(ids[0], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids[1], event.id, "yes", Decimal("30"), NOW, path)
    moderation.flag_event(ids[1], event.id, NOW, path)
    assert moderation.flag_event(ids[2], event.id, NOW, path) == moderation.DELETED
    assert stored_polls(path, event.id) == []
    assert_refunded(path, ids, group_id, event.id)


def test_single_flag_never_deletes_even_in_a_tiny_group(path):
    ids, _ = make_group(path, 1)
    event = service.create_event(ids[0], "Rain", CLOSE, NOW, path=path)
    assert moderation.flag_event(ids[0], event.id, NOW, path) == moderation.FLAGGED


def test_restricted_members_do_not_count(path):
    ids, group_id = make_group(path, 5)
    event = service.create_event(ids[0], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids[1], event.id, "yes", Decimal("30"), NOW, path)
    members.set_restricted(ids[0], group_id, ids[3], True, NOW, path)
    members.set_restricted(ids[0], group_id, ids[4], True, NOW, path)
    moderation.flag_event(ids[1], event.id, NOW, path)
    # 2 of 3 unrestricted members: more than half, so deleted (2 of 5 would only poll)
    assert moderation.flag_event(ids[2], event.id, NOW, path) == moderation.DELETED


def test_restricted_member_cannot_flag(path, four):
    ids, group_id, event_id = four
    members.set_restricted(ids[0], group_id, ids[2], True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        moderation.flag_event(ids[2], event_id, NOW, path)


def test_cannot_flag_twice(path, four):
    ids, _, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path)
    with pytest.raises(AuthError, match="already flagged"):
        moderation.flag_event(ids[2], event_id, NOW, path)
    assert stored_event(path, event_id)["flags"] == [ids[2]]


def test_cannot_flag_after_betting_closed_or_when_paused(path, four):
    ids, _, event_id = four
    with pytest.raises(AuthError, match="Only open events"):
        moderation.flag_event(ids[2], event_id, CLOSE, path)
    moderation.open_poll(ids[0], event_id, NOW, path)
    with pytest.raises(AuthError, match="Only open events"):
        moderation.flag_event(ids[2], event_id, NOW, path)


def test_outsider_cannot_flag(path, four):
    _, _, event_id = four
    outsider = auth.sign_up("outsider", PASSWORD, path).id
    with pytest.raises(AuthError, match="Event not found"):
        moderation.flag_event(outsider, event_id, NOW, path)


# --- polls ----------------------------------------------------------------------

def test_admin_opens_poll_directly(path, four):
    ids, _, event_id = four
    poll = moderation.open_poll(ids[0], event_id, NOW, path)
    assert (poll.opened_by, poll.closes_at) == (ids[0], NOW + timedelta(hours=1))
    assert stored_event(path, event_id)["status"] == "paused"


def test_poll_ends_halfway_when_close_is_near(path, four):
    ids, _, event_id = four
    soon = CLOSE - timedelta(hours=1)
    poll = moderation.open_poll(ids[0], event_id, soon, path)
    assert poll.closes_at == soon + timedelta(minutes=30)


def test_member_cannot_open_poll(path, four):
    ids, _, event_id = four
    with pytest.raises(AuthError, match="Only the group admin"):
        moderation.open_poll(ids[1], event_id, NOW, path)
    assert stored_polls(path, event_id) == []


def test_cannot_open_second_poll(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    with pytest.raises(AuthError, match="Only open events"):
        moderation.open_poll(ids[0], event_id, NOW, path)


def test_vote_is_stored_once(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    moderation.vote(ids[1], event_id, "keep", NOW, path)
    with pytest.raises(AuthError, match="already voted"):
        moderation.vote(ids[1], event_id, "delete", NOW, path)
    assert stored_polls(path, event_id)[0]["votes"] == {ids[1]: "keep"}


@pytest.mark.parametrize("choice", ["maybe", "", None])
def test_vote_must_be_delete_or_keep(path, four, choice):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    with pytest.raises(AuthError, match='"delete" or "keep"'):
        moderation.vote(ids[1], event_id, choice, NOW, path)


def test_cannot_vote_without_running_poll_or_after_it_ended(path, four):
    ids, _, event_id = four
    with pytest.raises(AuthError, match="no running poll"):
        moderation.vote(ids[1], event_id, "keep", NOW, path)
    moderation.open_poll(ids[0], event_id, NOW, path)
    with pytest.raises(AuthError, match="no running poll"):
        moderation.vote(ids[1], event_id, "keep", NOW + timedelta(hours=1), path)


def test_restricted_member_and_outsider_cannot_vote(path, four):
    ids, group_id, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    members.set_restricted(ids[0], group_id, ids[2], True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        moderation.vote(ids[2], event_id, "delete", NOW, path)
    outsider = auth.sign_up("outsider", PASSWORD, path).id
    with pytest.raises(AuthError, match="Event not found"):
        moderation.vote(outsider, event_id, "delete", NOW, path)


def poll_with_votes(path, four, votes):
    """Open a poll, cast the votes {member index: choice}, and end it via refresh()."""
    ids, group_id, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    for index, choice in votes.items():
        moderation.vote(ids[index], event_id, choice, NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    return stored_polls(path, event_id)[0]["result"], stored_event(path, event_id)


def test_majority_delete_deletes_with_refunds(path, four):
    ids, group_id, event_id = four
    result, _ = poll_with_votes(path, four, {1: "delete", 2: "delete", 3: "keep"})
    assert result == "deleted"
    assert_refunded(path, ids, group_id, event_id)


@pytest.mark.parametrize("votes", [
    {1: "delete", 2: "keep"},          # tie
    {},                                # nobody voted
    {1: "keep", 2: "keep", 3: "delete"},
])
def test_tie_no_votes_or_keep_majority_reopens_and_clears_flags(path, four, votes):
    ids, _, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path)
    result, event = poll_with_votes(path, four, votes)
    assert (result, event["status"], event["flags"]) == ("kept", "open", [])
    service.place_bet(ids[0], event_id, "yes", Decimal("5"), NOW + timedelta(hours=1), path)


def test_poll_is_not_ended_before_its_time(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    service.refresh(NOW + timedelta(minutes=59), path)
    assert stored_event(path, event_id)["status"] == "paused"


def test_page_load_ends_due_polls(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    service.page_data(ids[1], NOW + timedelta(hours=2), path)
    assert stored_polls(path, event_id)[0]["result"] == "kept"


def test_kept_event_past_close_is_closed_by_the_same_refresh(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    service.refresh(CLOSE, path)  # page opened only after betting closed
    assert stored_event(path, event_id)["status"] == "closed"


def test_refresh_without_changes_saves_nothing(path, four):
    ids, _, event_id = four
    before = len(storage.list_backups(path))
    assert service.refresh(NOW, path) == 0
    assert len(storage.list_backups(path)) == before


# --- what the feed card gets ------------------------------------------------------

def feed_item(path, user_id, event_id, now=NOW):
    """Return the feed summary of one event for the user."""
    return next(s for s in service.page_data(user_id, now, path).events
                if s.event.id == event_id)


def test_feed_card_gets_the_running_poll_and_the_flags(path, four):
    ids, _, event_id = four
    moderation.flag_event(ids[2], event_id, NOW, path)
    item = feed_item(path, ids[1], event_id)
    assert (item.poll, item.event.flags) == (None, [ids[2]])
    moderation.open_poll(ids[0], event_id, NOW, path)
    moderation.vote(ids[3], event_id, "keep", NOW, path)
    item = feed_item(path, ids[1], event_id)
    assert item.event.status == "paused"
    assert (item.poll.closes_at, item.poll.votes) == (NOW + timedelta(hours=1), {ids[3]: "keep"})


def test_feed_card_has_no_poll_after_it_ended(path, four):
    ids, _, event_id = four
    moderation.open_poll(ids[0], event_id, NOW, path)
    item = feed_item(path, ids[1], event_id, NOW + timedelta(hours=1))
    assert (item.poll, item.event.status) == (None, "open")

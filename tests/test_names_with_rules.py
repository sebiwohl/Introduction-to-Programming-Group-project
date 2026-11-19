"""Names events together with the member, moderation and balance rules.

The names events (Andre) and these rules (Sebastian) were built on separate
branches; these tests check that they work together after the merge.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, members, moderation, notifications, service, storage
from src.auth import AuthError
from src.betting import BettingError

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


@pytest.fixture
def names(path):
    """4 members (u0 admin, u1 creator) and a names event with bets on three names.

    Returns (ids, group id, event).
    """
    ids = [auth.sign_up(f"user{i}", PASSWORD, path).id for i in range(4)]
    group = auth.create_group(ids[0], "Friends", path=path)
    for user_id in ids[1:]:
        auth.join_group(user_id, group.invite_code, path)
    balances.top_up(ids[0], group.id, START, NOW - timedelta(hours=1), path)
    event = service.create_event(ids[1], "Who is late tonight?", CLOSE, NOW, path=path,
                                 options=["Anna", "Ben", "Carl"])
    for user_id, option, stake in ((ids[1], 0, "30"), (ids[2], 1, "50"), (ids[3], 2, "20")):
        service.place_bet(user_id, event.id, event.options[option].id, Decimal(stake), NOW, path)
    return ids, group.id, event


def balance(path, user_id, group_id):
    """Return the user's balance in the group."""
    return auth.user_in_group(storage.load_data(path), user_id, group_id).balance


def stored_event(path, event_id):
    """Return the stored event dict."""
    return next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)


def assert_refunded(path, ids, group_id, event_id):
    """Check the names event is deleted, valid, and everyone is back at the start balance."""
    data = storage.load_data(path)  # load_data validates, including names fields
    event = stored_event(path, event_id)
    assert (event["status"], event["winners"]) == ("deleted", [])
    assert all(balance(path, u, group_id) == START for u in ids)
    assert all(b["payout"] == b["stake"] for b in data["bets"] if b["event_id"] == event_id)


def test_creator_deletes_names_event_and_every_name_is_refunded(path, names):
    ids, group_id, event = names
    moderation.delete_event(ids[1], event.id, NOW, path)
    assert_refunded(path, ids, group_id, event.id)


def test_flags_delete_names_event_at_once_with_refunds(path, names):
    ids, group_id, event = names
    members.set_restricted(ids[0], group_id, ids[3], True, NOW, path)  # 3 members count
    moderation.flag_event(ids[1], event.id, NOW, path)
    assert moderation.flag_event(ids[2], event.id, NOW, path) == moderation.DELETED
    assert_refunded(path, ids, group_id, event.id)  # the restricted member is refunded too


def test_two_flags_in_four_pause_names_event_for_a_poll(path, names):
    ids, _, event = names
    moderation.flag_event(ids[1], event.id, NOW, path)
    assert moderation.flag_event(ids[2], event.id, NOW, path) == moderation.POLL_OPENED
    assert stored_event(path, event.id)["status"] == "paused"


def test_poll_deletes_names_event_with_refunds(path, names):
    ids, group_id, event = names
    moderation.open_poll(ids[0], event.id, NOW, path)
    for user_id in ids[1:3]:
        moderation.vote(user_id, event.id, "delete", NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    assert_refunded(path, ids, group_id, event.id)


def test_paused_names_event_takes_no_bets_and_reopens_after_keep(path, names):
    ids, _, event = names
    moderation.open_poll(ids[0], event.id, NOW, path)
    with pytest.raises(BettingError, match="not open"):
        service.place_bet(ids[0], event.id, event.options[0].id, Decimal("5"), NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)  # nobody voted: kept
    service.place_bet(ids[0], event.id, event.options[0].id, Decimal("5"),
                      NOW + timedelta(hours=1), path)
    assert stored_event(path, event.id)["status"] == "open"


def test_names_event_can_still_be_resolved_after_a_kept_poll(path, names):
    ids, group_id, event = names
    moderation.open_poll(ids[0], event.id, NOW, path)
    service.refresh(NOW + timedelta(hours=1), path)
    paid = service.resolve_names_event(ids[1], event.id, [event.options[1].id],
                                       CLOSE + timedelta(hours=1), path)
    assert paid == Decimal("100")
    assert balance(path, ids[2], group_id) == START - 50 + 100


def test_restricted_member_cannot_bet_on_a_name(path, names):
    ids, group_id, event = names
    members.set_restricted(ids[0], group_id, ids[3], True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        service.place_bet(ids[3], event.id, event.options[0].id, Decimal("5"), NOW, path)


def test_restricted_creator_cannot_resolve_names_event(path, names):
    ids, group_id, event = names
    members.set_restricted(ids[0], group_id, ids[1], True, NOW, path)
    with pytest.raises(AuthError, match="restricted"):
        service.resolve_names_event(ids[1], event.id, [event.options[0].id],
                                    CLOSE + timedelta(hours=1), path)


def test_member_at_zero_cannot_create_a_names_event(path, names):
    ids, _, event = names
    service.place_bet(ids[2], event.id, event.options[0].id, Decimal("450"), NOW, path)
    with pytest.raises(AuthError, match="Top-up required"):
        service.create_event(ids[2], "Who wins?", CLOSE, NOW, path=path, options=["A", "B"])


def test_low_balance_alert_after_a_bet_on_a_name(path, names):
    ids, group_id, event = names
    balances.top_up(ids[0], group_id, 100, NOW, path)  # user3: 480 + 100 = 580, line 10
    service.place_bet(ids[3], event.id, event.options[1].id, Decimal("575"), NOW, path)
    [note] = notifications.notifications_for(ids[0], path)
    assert note.text.startswith("user3 is running low in Friends")


def test_deleting_the_group_removes_names_events_and_their_bets(path, names):
    ids, group_id, event = names
    moderation.delete_event(ids[1], event.id, NOW, path)  # refunds, so everyone is settled
    for user_id in ids[1:]:
        members.leave_group(user_id, group_id, NOW, path)
    members.delete_group(ids[0], group_id, path)
    data = storage.load_data(path)
    assert (data["groups"], data["events"], data["bets"]) == ([], [], [])

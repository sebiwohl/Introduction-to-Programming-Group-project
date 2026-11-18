"""Tests for src/service.py: betting through real storage in a temporary file."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import service, storage
from src.betting import BettingError
from src.convert import event_to_dict, group_to_dict, membership_dicts, user_to_dict
from src.models import Event, Group, User

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
AFTER = CLOSE + timedelta(hours=1)


@pytest.fixture
def path(tmp_path):
    """Return a data file seeded with two groups, five users and three events.

    Group g1: admin u1 (Anna), members u2 (Ben) and u3 (Cleo).
    Group g2: admin u4 (Dan). User u5 (Eve) has no group.
    Events: e1 and e2 in g1 (e2 created by Ben), e9 in g2.
    """
    file = tmp_path / "data.json"
    users = [
        User(id="u1", username="Anna", password_hash="h", group_id="g1",
             role="admin", balance=Decimal("100")),
        User(id="u2", username="Ben", password_hash="h", group_id="g1",
             balance=Decimal("100")),
        User(id="u3", username="Cleo", password_hash="h", group_id="g1",
             balance=Decimal("100")),
        User(id="u4", username="Dan", password_hash="h", group_id="g2",
             role="admin", balance=Decimal("100")),
        User(id="u5", username="Eve", password_hash="h", balance=Decimal("100")),
    ]
    groups = [
        Group(id="g1", name="Friends", admin_id="u1", invite_code="AAA"),
        Group(id="g2", name="Others", admin_id="u4", invite_code="BBB"),
    ]
    events = [
        Event(id="e1", group_id="g1", creator_id="u1", title="Anna is late",
              close_time=CLOSE),
        Event(id="e2", group_id="g1", creator_id="u2", title="Rain on Saturday",
              close_time=CLOSE + timedelta(days=1)),
        Event(id="e9", group_id="g2", creator_id="u4", title="Secret event",
              close_time=CLOSE),
    ]
    data = storage.empty_data()
    data["users"] = [user_to_dict(u) for u in users]
    data["memberships"] = membership_dicts(users)
    data["groups"] = [group_to_dict(g) for g in groups]
    data["events"] = [event_to_dict(e) for e in events]
    storage.save_data(data, file)
    return file


def balance(path, user_id):
    """Return a user's saved balance."""
    return service.get_user(user_id, path).balance


# --- place_bet ----------------------------------------------------------------

def test_place_bet_saves_bet_and_balance_together(path):
    bet = service.place_bet("u2", "e1", "yes", Decimal("12.50"), NOW, path)
    assert balance(path, "u2") == Decimal("87.50")
    saved = storage.load_data(path)
    assert [b["id"] for b in saved["bets"]] == [bet.id]
    assert saved["bets"][0]["stake"] == 1250  # stored as cents


def test_rejected_bet_saves_nothing(path):
    before = path.read_bytes()
    with pytest.raises(BettingError):
        service.place_bet("u2", "e1", "yes", Decimal("500"), NOW, path)
    assert path.read_bytes() == before


def test_cannot_bet_on_other_groups_event(path):
    with pytest.raises(BettingError, match="Event not found"):
        service.place_bet("u2", "e9", "yes", 10, NOW, path)
    assert balance(path, "u2") == 100


def test_unknown_event_and_other_group_give_same_message(path):
    messages = []
    for event_id in ("e9", "nope"):
        with pytest.raises(BettingError) as error:
            service.place_bet("u2", event_id, "yes", 10, NOW, path)
        messages.append(str(error.value))
    assert messages[0] == messages[1]


def test_unknown_user_is_rejected(path):
    with pytest.raises(BettingError, match="Unknown user"):
        service.place_bet("ghost", "e1", "yes", 10, NOW, path)


def test_bet_after_close_time_is_rejected(path):
    with pytest.raises(BettingError):
        service.place_bet("u2", "e1", "yes", 10, AFTER, path)
    assert balance(path, "u2") == 100


def test_naive_time_is_rejected(path):
    with pytest.raises(BettingError, match="timezone"):
        service.place_bet("u2", "e1", "yes", 10, datetime(2026, 11, 20, 12, 0), path)


def test_balances_add_up_across_several_bets(path):
    service.place_bet("u2", "e1", "yes", 10, NOW, path)
    service.place_bet("u2", "e2", "no", Decimal("5.25"), NOW, path)
    assert balance(path, "u2") == Decimal("84.75")


# --- resolve_event --------------------------------------------------------------

def test_resolve_pays_out_and_saves(path):
    service.place_bet("u2", "e1", "yes", 10, NOW, path)
    service.place_bet("u3", "e1", "yes", 10, NOW, path)
    service.place_bet("u1", "e1", "no", 10, NOW, path)
    assert service.resolve_event("u1", "e1", "yes", AFTER, path) == 30
    assert balance(path, "u2") == 105
    assert balance(path, "u3") == 105
    assert balance(path, "u1") == 90
    saved = {e["id"]: e for e in storage.load_data(path)["events"]}
    assert (saved["e1"]["status"], saved["e1"]["outcome"]) == ("resolved", "yes")


def test_creator_can_resolve_own_event(path):
    service.resolve_event("u2", "e2", "no", AFTER + timedelta(days=1), path)


def test_admin_can_resolve_any_event_in_group(path):
    service.resolve_event("u1", "e2", "no", AFTER + timedelta(days=1), path)


def test_member_cannot_resolve_others_event(path):
    with pytest.raises(BettingError, match="creator or the group admin"):
        service.resolve_event("u3", "e1", "yes", AFTER, path)


def test_other_groups_admin_cannot_resolve(path):
    with pytest.raises(BettingError, match="Event not found"):
        service.resolve_event("u4", "e1", "yes", AFTER, path)


def test_cannot_resolve_before_close_time(path):
    with pytest.raises(BettingError):
        service.resolve_event("u1", "e1", "yes", NOW, path)
    assert storage.load_data(path)["events"][0]["status"] == "open"


def test_cannot_resolve_twice(path):
    service.resolve_event("u1", "e1", "yes", AFTER, path)
    with pytest.raises(BettingError):
        service.resolve_event("u1", "e1", "no", AFTER, path)


def test_resolve_with_bets_on_one_side_refunds(path):
    service.place_bet("u2", "e1", "yes", Decimal("7.77"), NOW, path)
    service.resolve_event("u1", "e1", "no", AFTER, path)
    assert balance(path, "u2") == 100


# --- create_event ---------------------------------------------------------------

def test_create_event_in_own_group(path):
    event = service.create_event("u3", "  Will Ben cook?  ", CLOSE, NOW, path=path)
    assert (event.group_id, event.creator_id, event.title) == ("g1", "u3", "Will Ben cook?")
    assert event.id in [s.event.id for s in service.feed("u2", NOW, path)]


@pytest.mark.parametrize("title", ["", "   ", "x" * 121, None])
def test_create_event_rejects_bad_title(path, title):
    with pytest.raises(BettingError):
        service.create_event("u2", title, CLOSE, NOW, path=path)


def test_create_event_rejects_past_close_time(path):
    with pytest.raises(BettingError, match="future"):
        service.create_event("u2", "Too late", NOW, NOW, path=path)


def test_create_event_needs_a_group(path):
    with pytest.raises(BettingError, match="group"):
        service.create_event("u5", "Lonely", CLOSE, NOW, path=path)


# --- feed and details -----------------------------------------------------------

def test_feed_shows_only_own_group_with_numbers(path):
    service.place_bet("u2", "e1", "yes", 40, NOW, path)
    service.place_bet("u3", "e1", "no", 10, NOW, path)
    items = service.feed("u2", NOW, path)
    assert [i.event.id for i in items] == ["e1", "e2"]
    first = items[0]
    assert (first.pool, first.stake_yes, first.stake_no) == (50, 40, 10)
    assert (first.odds_yes, first.odds_no) == (1.25, 5.0)
    assert first.my_stake == 40
    assert items[1].odds_yes is None  # nobody has bet yet


def test_feed_closes_expired_events(path):
    items = {i.event.id: i.event.status for i in service.feed("u1", AFTER, path)}
    assert items == {"e1": "closed", "e2": "open"}
    saved = {e["id"]: e["status"] for e in storage.load_data(path)["events"]}
    assert saved["e1"] == "closed" and saved["e9"] == "closed"


def test_feed_lists_open_events_first(path):
    order = [i.event.id for i in service.feed("u1", AFTER, path)]
    assert order == ["e2", "e1"]


def test_viewing_pages_without_changes_does_not_save(path):
    before = path.read_bytes()
    service.feed("u1", NOW, path)
    service.event_details("u1", "e1", NOW, path)
    assert path.read_bytes() == before
    assert storage.list_backups(path) == []


def test_event_details_of_other_group_is_hidden(path):
    with pytest.raises(BettingError, match="Event not found"):
        service.event_details("u2", "e9", NOW, path)


def test_user_without_group_sees_empty_feed(path):
    assert service.feed("u5", NOW, path) == []


# --- history and leaderboard ----------------------------------------------------

def test_bet_history_shows_results(path):
    service.place_bet("u2", "e1", "yes", 10, NOW, path)
    service.place_bet("u3", "e1", "no", 10, NOW, path)
    service.resolve_event("u1", "e1", "yes", AFTER, path)
    history = service.bet_history("u2", path)
    assert len(history) == 1
    record = history[0]
    assert (record.event_title, record.event_status, record.outcome) == (
        "Anna is late", "resolved", "yes")
    assert record.bet.payout == 20
    assert service.bet_history("u1", path) == []


def test_leaderboard_ranks_own_group_only(path):
    service.place_bet("u2", "e1", "yes", 10, NOW, path)
    service.place_bet("u3", "e1", "no", 10, NOW, path)
    service.resolve_event("u1", "e1", "no", AFTER, path)
    rows = service.leaderboard("u2", path)
    assert [(r.rank, r.username, r.balance) for r in rows] == [
        (1, "Cleo", 110), (2, "Anna", 100), (3, "Ben", 90)]


def test_leaderboard_ties_share_a_rank(path):
    rows = service.leaderboard("u1", path)
    assert [(r.rank, r.username) for r in rows] == [(1, "Anna"), (1, "Ben"), (1, "Cleo")]


def test_leaderboard_without_group_is_empty(path):
    assert service.leaderboard("u5", path) == []


# --- get_group ----------------------------------------------------------------

def test_get_group_returns_own_group(path):
    """A member gets their own group, with its name and invite code."""
    group = service.get_group("u2", path)
    assert (group.id, group.name, group.invite_code) == ("g1", "Friends", "AAA")


def test_get_group_never_returns_another_group(path):
    """Each user only ever gets the group they belong to."""
    assert service.get_group("u4", path).id == "g2"
    assert service.get_group("u1", path).id == "g1"


def test_get_group_without_group_is_none(path):
    """A user who has not joined a group gets None."""
    assert service.get_group("u5", path) is None


def test_get_group_unknown_user_rejected(path):
    """An unknown user id is rejected."""
    with pytest.raises(BettingError):
        service.get_group("nobody", path)


# --- page_data ----------------------------------------------------------------

def test_page_data_matches_the_separate_functions(path):
    """page_data returns the same numbers as feed, bet_history, leaderboard and get_group."""
    service.place_bet("u2", "e1", "yes", Decimal("10"), NOW, path)
    service.place_bet("u3", "e1", "no", Decimal("30"), NOW, path)
    page = service.page_data("u2", NOW, path)
    assert page.user == service.get_user("u2", path)
    assert page.group == service.get_group("u2", path)
    assert page.events == service.feed("u2", NOW, path)
    assert page.my_bets == service.bet_history("u2", path)
    assert page.leaderboard == service.leaderboard("u2", path)


def test_page_data_reads_the_data_once(path, monkeypatch):
    """With nothing to close, the page is built from a single load."""
    loads = []
    real_load = storage.load_data
    monkeypatch.setattr(storage, "load_data", lambda p=None: loads.append(p) or real_load(p))
    service.page_data("u1", NOW, path)
    assert len(loads) == 1


def test_page_data_closes_expired_events(path):
    """Events past their close time are closed and saved, like feed() does."""
    page = service.page_data("u1", AFTER, path)
    status = {s.event.id: s.event.status for s in page.events}
    assert status["e1"] == "closed"
    saved = {e["id"]: e["status"] for e in storage.load_data(path)["events"]}
    assert saved["e1"] == "closed"


def test_page_data_only_shows_own_group(path):
    """A member never sees another group's events or members."""
    page = service.page_data("u1", NOW, path)
    assert "e9" not in {s.event.id for s in page.events}
    assert {r.username for r in page.leaderboard} == {"Anna", "Ben", "Cleo"}


def test_page_data_user_without_group(path):
    """A user without a group gets no group, no events and an empty leaderboard."""
    page = service.page_data("u5", NOW, path)
    assert (page.group, page.events, page.leaderboard) == (None, [], [])


def test_page_data_unknown_user_rejected(path):
    """An unknown user id is rejected."""
    with pytest.raises(BettingError):
        service.page_data("nobody", NOW, path)


def test_page_data_needs_aware_time(path):
    """A time without a timezone is rejected."""
    with pytest.raises(BettingError):
        service.page_data("u1", datetime(2026, 11, 20, 12, 0), path)


def test_set_avatar_saves_the_choice(path):
    service.set_avatar("u2", "panda", path)
    assert service.get_user("u2", path).avatar == "panda"
    assert service.get_user("u1", path).avatar == ""


def test_set_avatar_rejects_unknown_picture(path):
    with pytest.raises(BettingError):
        service.set_avatar("u2", "dragon", path)
    assert service.get_user("u2", path).avatar == ""


def test_set_avatar_rejects_unknown_user(path):
    with pytest.raises(BettingError):
        service.set_avatar("nobody", "fox", path)


def test_leaderboard_rows_carry_each_members_picture(path):
    service.set_avatar("u2", "owl", path)
    pictures = {r.username: r.avatar for r in service.leaderboard("u1", path)}
    assert pictures == {"Anna": "", "Ben": "owl", "Cleo": ""}

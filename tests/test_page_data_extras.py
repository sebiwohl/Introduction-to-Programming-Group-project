"""Tests for the read-only extras in service.page_data(): bettors, creator, admin, open bets."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import service, storage
from src.convert import event_to_dict, group_to_dict, user_to_dict
from src.models import Event, Group, User

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
AFTER = CLOSE + timedelta(hours=1)


@pytest.fixture
def path(tmp_path):
    """Return a data file with group g1 (admin Anna, members Ben and Cleo) and g2 (Dan).

    Events: e1 (by Anna) and e2 (by Ben) in g1, e9 in g2.
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
    data["groups"] = [group_to_dict(g) for g in groups]
    data["events"] = [event_to_dict(e) for e in events]
    storage.save_data(data, file)
    return file


def summary_of(page, event_id):
    """Return the feed summary of one event."""
    return next(s for s in page.events if s.event.id == event_id)


def row_of(page, username):
    """Return one member's leaderboard row."""
    return next(r for r in page.leaderboard if r.username == username)


def test_summary_names_the_creator(path):
    page = service.page_data("u3", NOW, path)
    assert summary_of(page, "e1").creator_name == "Anna"
    assert summary_of(page, "e2").creator_name == "Ben"


def test_bettors_add_up_stakes_per_member_and_side(path):
    service.place_bet("u2", "e1", "yes", Decimal("10"), NOW, path)
    service.place_bet("u2", "e1", "yes", Decimal("5"), NOW, path)
    service.place_bet("u2", "e1", "no", Decimal("2"), NOW, path)
    service.place_bet("u3", "e1", "yes", Decimal("20"), NOW, path)
    bettors = summary_of(service.page_data("u1", NOW, path), "e1").bettors
    assert [(b.username, b.side, b.stake) for b in bettors] == [
        ("Cleo", "yes", Decimal("20")),
        ("Ben", "yes", Decimal("15")),
        ("Ben", "no", Decimal("2")),
    ]


def test_bettors_empty_without_bets(path):
    assert summary_of(service.page_data("u1", NOW, path), "e1").bettors == []


def test_bettors_never_include_other_groups(path):
    service.place_bet("u4", "e9", "yes", Decimal("10"), NOW, path)
    page = service.page_data("u1", NOW, path)
    assert all(s.event.id != "e9" for s in page.events)
    assert all(b.username != "Dan" for s in page.events for b in s.bettors)


def test_leaderboard_marks_only_the_group_admin(path):
    page = service.page_data("u2", NOW, path)
    assert row_of(page, "Anna").is_admin
    assert not row_of(page, "Ben").is_admin
    assert not row_of(page, "Cleo").is_admin
    assert all(r.username != "Dan" for r in page.leaderboard)


def test_open_bets_count_unsettled_bets_only(path):
    service.place_bet("u2", "e1", "yes", Decimal("10"), NOW, path)
    service.place_bet("u2", "e2", "no", Decimal("10"), NOW, path)
    service.place_bet("u3", "e1", "no", Decimal("10"), NOW, path)
    page = service.page_data("u1", NOW, path)
    assert row_of(page, "Ben").open_bets == 2
    assert row_of(page, "Cleo").open_bets == 1
    assert row_of(page, "Anna").open_bets == 0


def test_closed_event_bets_still_count_as_open_until_resolved(path):
    service.place_bet("u2", "e1", "yes", Decimal("10"), NOW, path)
    service.place_bet("u3", "e1", "no", Decimal("10"), NOW, path)
    assert row_of(service.page_data("u1", AFTER, path), "Ben").open_bets == 1
    service.resolve_event("u1", "e1", "yes", AFTER, path)
    page = service.page_data("u1", AFTER, path)
    assert row_of(page, "Ben").open_bets == 0
    assert row_of(page, "Cleo").open_bets == 0

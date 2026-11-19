"""Tests for the win announcements in src/service.py, which decide who gets confetti.

When a result is set, each member who won coins gets an unread notification; the
Feed shows confetti for unread ones once and then marks them read.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import service, storage
from src.convert import event_to_dict, group_to_dict, membership_dicts, user_to_dict
from src.models import Event, Group, User

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
AFTER = CLOSE + timedelta(hours=1)


@pytest.fixture
def path(tmp_path):
    """Return a data file with group g1 (Anna admin u1, Ben u2, Cleo u3) and yes/no events e1, e2."""
    file = tmp_path / "data.json"
    users = [User(id="u1", username="Anna", password_hash="h", group_id="g1", role="admin",
                  balance=Decimal("100")),
             User(id="u2", username="Ben", password_hash="h", group_id="g1", balance=Decimal("100")),
             User(id="u3", username="Cleo", password_hash="h", group_id="g1", balance=Decimal("100"))]
    data = storage.empty_data()
    data["users"] = [user_to_dict(u) for u in users]
    data["memberships"] = membership_dicts(users)
    data["groups"] = [group_to_dict(Group(id="g1", name="Friends", admin_id="u1", invite_code="A"))]
    data["events"] = [event_to_dict(Event(id=e, group_id="g1", creator_id="u1", title=f"Event {e}",
                                          close_time=CLOSE)) for e in ("e1", "e2")]
    storage.save_data(data, file)
    return file


def new_wins(path, user_id):
    """Return (event title, amount) of the user's unseen wins, as the Feed gets them."""
    return [(w.event_title, w.amount) for w in service.page_data(user_id, AFTER, path).new_wins]


def test_winner_gets_a_new_win_and_loser_does_not(path):
    service.place_bet("u1", "e1", "yes", Decimal("30"), NOW, path)
    service.place_bet("u2", "e1", "no", Decimal("10"), NOW, path)
    service.resolve_event("u1", "e1", "no", AFTER, path)
    assert new_wins(path, "u2") == [("Event e1", Decimal("30"))]  # paid 40 on a 10 stake
    assert new_wins(path, "u1") == []
    assert service.has_new_win("u2", path) and not service.has_new_win("u1", path)


def test_each_win_is_celebrated_once(path):
    service.place_bet("u1", "e1", "yes", Decimal("30"), NOW, path)
    service.place_bet("u2", "e1", "no", Decimal("10"), NOW, path)
    service.resolve_event("u1", "e1", "no", AFTER, path)
    ids = [w.notification_id for w in service.page_data("u2", AFTER, path).new_wins]
    service.mark_wins_seen("u2", ids, path)
    assert new_wins(path, "u2") == [] and not service.has_new_win("u2", path)
    note = next(n for n in storage.load_data(path)["notifications"] if n["user_id"] == "u2")
    assert note["read"] is True


def test_nobody_can_mark_someone_elses_win_as_seen(path):
    service.place_bet("u1", "e1", "yes", Decimal("30"), NOW, path)
    service.place_bet("u2", "e1", "no", Decimal("10"), NOW, path)
    service.resolve_event("u1", "e1", "no", AFTER, path)
    ids = [w.notification_id for w in service.page_data("u2", AFTER, path).new_wins]
    service.mark_wins_seen("u1", ids, path)
    assert new_wins(path, "u2") == [("Event e1", Decimal("30"))]


def test_refunds_get_no_confetti(path):
    service.place_bet("u1", "e1", "yes", Decimal("30"), NOW, path)
    service.place_bet("u2", "e1", "yes", Decimal("10"), NOW, path)  # only one side: everyone refunded
    service.resolve_event("u1", "e1", "yes", AFTER, path)
    assert new_wins(path, "u1") == [] and new_wins(path, "u2") == []
    assert storage.load_data(path)["notifications"] == []


def test_backing_both_sides_counts_when_it_made_coins_overall(path):
    service.place_bet("u1", "e1", "yes", Decimal("10"), NOW, path)
    service.place_bet("u1", "e1", "no", Decimal("30"), NOW, path)  # Anna hedges
    service.place_bet("u2", "e1", "no", Decimal("60"), NOW, path)
    service.place_bet("u1", "e2", "yes", Decimal("10"), NOW, path)
    service.place_bet("u1", "e2", "no", Decimal("30"), NOW, path)  # and again
    service.place_bet("u3", "e2", "yes", Decimal("90"), NOW, path)
    service.resolve_event("u1", "e1", "yes", AFTER, path)  # Anna: 100 back on 40 staked
    service.resolve_event("u1", "e2", "yes", AFTER, path)  # Anna: 13 back on 40 staked
    assert new_wins(path, "u1") == [("Event e1", Decimal("60"))]  # e2 lost her coins overall
    assert new_wins(path, "u3") == [("Event e2", Decimal("27"))]  # Cleo: 117 back on 90
    assert new_wins(path, "u2") == []  # Ben's bet on e1 lost


def test_several_new_wins_are_all_listed(path):
    for event in ("e1", "e2"):
        service.place_bet("u1", event, "yes", Decimal("10"), NOW, path)
        service.place_bet("u2", event, "no", Decimal("20"), NOW, path)
    for event in ("e1", "e2"):
        service.resolve_event("u1", event, "yes", AFTER, path)
    assert new_wins(path, "u1") == [("Event e1", Decimal("20")), ("Event e2", Decimal("20"))]


def test_names_event_with_two_winners_announces_both(path):
    event = service.create_event("u2", "Who is late?", CLOSE, NOW, path=path,
                                 options=["Tiago", "Marta", "Rui"], multi_winner=True)
    service.place_bet("u1", event.id, "o1", Decimal("20"), NOW, path)
    service.place_bet("u2", event.id, "o2", Decimal("20"), NOW, path)
    service.place_bet("u3", event.id, "o3", Decimal("40"), NOW, path)
    service.resolve_names_event("u2", event.id, ["o1", "o2"], AFTER, path)  # 80 / 40 = 2 per coin
    assert new_wins(path, "u1") == [("Who is late?", Decimal("20"))]
    assert new_wins(path, "u2") == [("Who is late?", Decimal("20"))]
    assert new_wins(path, "u3") == []


def test_wins_from_before_announcements_existed_get_no_confetti(path):
    """Old data has resolved events but no win notifications: nothing to celebrate."""
    data = storage.load_data(path)
    data["events"][0].update({"status": "resolved", "outcome": "yes"})
    data["bets"] = [
        {"id": "b1", "event_id": "e1", "user_id": "u1", "side": "yes", "stake": 1000, "payout": 3000},
        {"id": "b2", "event_id": "e1", "user_id": "u2", "side": "no", "stake": 2000, "payout": 0},
    ]
    storage.save_data(data, path)
    assert new_wins(path, "u1") == [] and not service.has_new_win("u1", path)

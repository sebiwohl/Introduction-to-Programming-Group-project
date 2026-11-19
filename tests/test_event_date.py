"""Tests for the event date: the "Betting closes" choices and no result before the event.

An event has a date (event_time) and a close time. Betting closes no later than
the event date, and the result can only be set once the event has happened.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src import betting, service, storage
from src.betting import BettingError
from src.convert import group_to_dict, membership_dicts, user_to_dict
from src.models import Group, User

UTC = timezone.utc
LISBON = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)  # a Friday
CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
EVENT = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
BETWEEN = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)  # betting closed, the event not happened yet
AFTER = EVENT + timedelta(minutes=30)
KINDS = ("yes_no", "over_under", "names")


@pytest.fixture
def path(tmp_path):
    """Return a data file with group g1: Anna (admin u1) and Ben (u2), 100 coins each."""
    file = tmp_path / "data.json"
    users = [User(id="u1", username="Anna", password_hash="h", group_id="g1", role="admin",
                  balance=Decimal("100")),
             User(id="u2", username="Ben", password_hash="h", group_id="g1", balance=Decimal("100"))]
    data = storage.empty_data()
    data["users"] = [user_to_dict(u) for u in users]
    data["memberships"] = membership_dicts(users)
    data["groups"] = [group_to_dict(Group(id="g1", name="Friends", admin_id="u1", invite_code="A"))]
    storage.save_data(data, file)
    return file


def create(path, kind, event_time=EVENT):
    """Create an event of this kind (closing at CLOSE) with a bet on each side; return its id."""
    extra = {"names": {"options": ["Tiago", "Marta"]}, "over_under": {"line": Decimal("2.5")}}
    event = service.create_event("u1", f"A {kind} event", CLOSE, NOW, path=path,
                                 event_time=event_time, **extra.get(kind, {}))
    first, second = ("o1", "o2") if kind == "names" else ("yes", "no")
    service.place_bet("u1", event.id, first, Decimal("30"), NOW, path)
    service.place_bet("u2", event.id, second, Decimal("10"), NOW, path)
    return event.id


def resolve(path, kind, event_id, now):
    """Set the result of the event (the first side wins); return the total paid."""
    if kind == "names":
        return service.resolve_names_event("u1", event_id, ["o1"], now, path)
    if kind == "over_under":
        return service.resolve_over_under("u1", event_id, Decimal("3"), now, path)
    return service.resolve_event("u1", event_id, "yes", now, path)


def stored_event(path, event_id):
    """Return the event as it is saved."""
    return next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)


# --- the "Betting closes" choices ---------------------------------------------------

@pytest.mark.parametrize("choice, before", [
    (betting.CLOSE_AT_START, timedelta(0)),
    (betting.CLOSE_HOUR_BEFORE, timedelta(hours=1)),
    (betting.CLOSE_DAY_BEFORE, timedelta(days=1)),
])
def test_each_preset_gives_its_close_time(choice, before):
    assert betting.close_time_for(EVENT, choice) == EVENT - before


def test_the_choices_are_the_four_options_without_never():
    assert betting.CLOSE_CHOICES == ("At the event start", "1 hour before", "1 day before", "Custom")


def test_custom_uses_the_picked_time():
    custom = EVENT - timedelta(hours=5, minutes=15)
    assert betting.close_time_for(EVENT, betting.CLOSE_CUSTOM, custom) == custom


def test_custom_may_close_exactly_at_the_event_date():
    assert betting.close_time_for(EVENT, betting.CLOSE_CUSTOM, EVENT) == EVENT


def test_custom_after_the_event_date_is_rejected():
    with pytest.raises(BettingError, match="no later than the event date"):
        betting.close_time_for(EVENT, betting.CLOSE_CUSTOM, EVENT + timedelta(minutes=1))


def test_custom_without_a_time_is_rejected():
    with pytest.raises(BettingError, match="Pick the day and hour"):
        betting.close_time_for(EVENT, betting.CLOSE_CUSTOM)


@pytest.mark.parametrize("choice", ["Never", "", None, "1 week before"])
def test_unknown_choices_are_rejected(choice):
    with pytest.raises(BettingError, match="Choose when betting closes"):
        betting.close_time_for(EVENT, choice)


def test_times_without_a_timezone_are_rejected():
    with pytest.raises(BettingError, match="timezone"):
        betting.close_time_for(datetime(2026, 11, 20, 21, 0), betting.CLOSE_AT_START)
    with pytest.raises(BettingError, match="timezone"):
        betting.close_time_for(EVENT, betting.CLOSE_CUSTOM, datetime(2026, 11, 20, 18, 0))


def test_one_hour_before_is_sixty_minutes_when_the_clocks_go_forward():
    """Lisbon jumps from 01:00 to 02:00 on 28 March 2027; 02:30 is 01:30 UTC."""
    event = datetime(2027, 3, 28, 2, 30, tzinfo=LISBON)
    closes = betting.close_time_for(event, betting.CLOSE_HOUR_BEFORE)
    assert event.astimezone(UTC) - closes.astimezone(UTC) == timedelta(hours=1)
    assert closes.astimezone(LISBON).strftime("%H:%M") == "00:30"


def test_one_day_before_keeps_the_clock_time_when_the_clocks_change():
    event = datetime(2027, 3, 29, 18, 0, tzinfo=LISBON)
    closes = betting.close_time_for(event, betting.CLOSE_DAY_BEFORE).astimezone(LISBON)
    assert (closes.day, closes.hour, closes.minute) == (28, 18, 0)


# --- creating an event with a date ---------------------------------------------------

def test_create_event_saves_the_event_date(path):
    event = service.create_event("u1", "Dinner", CLOSE, NOW, path=path, event_time=EVENT)
    assert event.event_time == EVENT
    saved = stored_event(path, event.id)
    assert saved["event_time"] == EVENT.isoformat() and saved["close_time"] == CLOSE.isoformat()


def test_without_an_event_date_the_event_happens_when_betting_closes(path):
    event = service.create_event("u1", "Dinner", CLOSE, NOW, path=path)
    assert event.event_time == CLOSE
    assert stored_event(path, event.id)["event_time"] == CLOSE.isoformat()


def test_betting_closing_after_the_event_date_is_rejected(path):
    with pytest.raises(BettingError, match="no later than the event date"):
        service.create_event("u1", "Dinner", EVENT + timedelta(minutes=1), NOW, path=path,
                             event_time=EVENT)
    assert storage.load_data(path)["events"] == []


def test_a_preset_that_lands_in_the_past_is_rejected(path):
    soon = NOW + timedelta(minutes=2)  # one hour before is 58 minutes ago
    closes = betting.close_time_for(soon, betting.CLOSE_HOUR_BEFORE)
    with pytest.raises(BettingError, match="Betting would already be closed"):
        service.create_event("u1", "Dinner", closes, NOW, path=path, event_time=soon)
    assert storage.load_data(path)["events"] == []


def test_an_event_date_without_a_timezone_is_rejected(path):
    with pytest.raises(BettingError, match="timezone"):
        service.create_event("u1", "Dinner", CLOSE, NOW, path=path,
                             event_time=datetime(2026, 11, 20, 21, 0))


def test_every_type_saves_its_event_date(path):
    for kind in KINDS:
        assert stored_event(path, create(path, kind))["event_time"] == EVENT.isoformat()


# --- betting closes, the result waits for the event -----------------------------------

def test_betting_closes_at_the_close_time_not_the_event_date(path):
    event_id = create(path, "yes_no")
    service.refresh(BETWEEN, path)
    assert stored_event(path, event_id)["status"] == "closed"
    with pytest.raises(BettingError, match="not open"):
        service.place_bet("u2", event_id, "no", Decimal("5"), BETWEEN, path)


@pytest.mark.parametrize("kind", KINDS)
def test_result_before_the_event_date_is_rejected(path, kind):
    event_id = create(path, kind)
    service.refresh(BETWEEN, path)
    before = storage.load_data(path)
    with pytest.raises(BettingError, match=r"after the event date \(Fri 20 Nov, 21:00 UTC\)"):
        resolve(path, kind, event_id, BETWEEN)
    assert storage.load_data(path) == before  # nothing paid, nothing changed


@pytest.mark.parametrize("kind", KINDS)
def test_result_after_the_event_date_is_allowed(path, kind):
    event_id = create(path, kind)
    assert resolve(path, kind, event_id, AFTER) == Decimal("40")
    assert stored_event(path, event_id)["status"] == "resolved"


@pytest.mark.parametrize("kind", KINDS)
def test_result_exactly_at_the_event_date_is_allowed(path, kind):
    event_id = create(path, kind)
    assert resolve(path, kind, event_id, EVENT) == Decimal("40")


@pytest.mark.parametrize("kind", KINDS)
def test_old_events_still_resolve_at_their_close_time(path, kind):
    """Events created before the event date existed have event_time == close_time."""
    event_id = create(path, kind, event_time=None)
    assert stored_event(path, event_id)["event_time"] == stored_event(path, event_id)["close_time"]
    assert resolve(path, kind, event_id, CLOSE) == Decimal("40")


def test_before_the_close_time_the_close_time_message_comes_first(path):
    event_id = create(path, "yes_no")
    with pytest.raises(BettingError, match="before its close time"):
        resolve(path, "yes_no", event_id, CLOSE - timedelta(minutes=1))

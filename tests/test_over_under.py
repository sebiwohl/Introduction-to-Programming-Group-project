"""Tests for over/under events: the line, betting over or under, and the actual number."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, betting, service, storage
from src.auth import AuthError
from src.betting import BettingError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLOSE = NOW + timedelta(days=2)
AFTER = CLOSE + timedelta(hours=1)
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
    """anna (admin), ben, carl topped up 100; dan is outside. Returns ids."""
    ids = {name: auth.sign_up(name, PASSWORD, path).id for name in ("anna", "ben", "carl", "dan")}
    friends = auth.create_group(ids["anna"], "Friends", path=path)
    for name in ("ben", "carl"):
        auth.join_group(ids[name], friends.invite_code, path, now=NOW)
    balances.top_up(ids["anna"], friends.id, 100, NOW, path)
    return ids


@pytest.fixture
def goals(path, group):
    """ben's 'Goals' event with line 2.5: ben 30 over, carl 10 under. Returns (ids, event id)."""
    ids = group
    event = service.create_event(ids["ben"], "Goals", CLOSE, NOW, path=path, line=Decimal("2.5"))
    service.place_bet(ids["ben"], event.id, "yes", Decimal("30"), NOW, path)
    service.place_bet(ids["carl"], event.id, "no", Decimal("10"), NOW, path)
    return ids, event.id


def balance(path, user_id):
    """Return the user's balance."""
    return service.get_user(user_id, path).balance


def stored_event(path, event_id):
    """Return the stored event dict."""
    return next(e for e in storage.load_data(path)["events"] if e["id"] == event_id)


# --- creating --------------------------------------------------------------------

def test_create_over_under_with_a_line(path, group):
    event = service.create_event(group["ben"], "Goals", CLOSE, NOW, path=path,
                                 line=Decimal("2.5"))
    stored = stored_event(path, event.id)
    assert (stored["type"], stored["line"], stored["result_value"]) == ("over_under", "2.5", None)


@pytest.mark.parametrize("line, text", [(3, "3"), (Decimal("-1.25"), "-1.25"),
                                         (Decimal("100.00"), "100"), (Decimal("2.50"), "2.5"),
                                         (Decimal("300"), "300")])
def test_lines_are_stored_as_plain_numbers(path, group, line, text):
    event = service.create_event(group["ben"], "Goals", CLOSE, NOW, path=path, line=line)
    assert stored_event(path, event.id)["line"] == text


@pytest.mark.parametrize("line", [2.5, True, "2.5", Decimal("2.555"), Decimal("NaN"),
                                  Decimal("1000001")])
def test_bad_line_is_rejected_and_nothing_saved(path, group, line):
    with pytest.raises(BettingError, match="line"):
        service.create_event(group["ben"], "Goals", CLOSE, NOW, path=path, line=line)
    assert storage.load_data(path)["events"] == []


def test_cannot_be_names_and_over_under_at_once(path, group):
    with pytest.raises(BettingError, match="not both"):
        service.create_event(group["ben"], "Goals", CLOSE, NOW, path=path,
                             options=["A", "B"], line=Decimal("2.5"))


# --- betting and labels ------------------------------------------------------------

def test_bets_use_over_and_under_labels(path, goals):
    ids, event_id = goals
    item = next(s for s in service.page_data(ids["carl"], NOW, path).events
                if s.event.id == event_id)
    assert betting.side_label(item.event, "yes") == "OVER 2.5"
    assert betting.side_label(item.event, "no") == "UNDER 2.5"
    [record] = [r for r in service.page_data(ids["carl"], NOW, path).my_bets
                if r.bet.event_id == event_id]
    assert record.side_label == "UNDER 2.5"


def test_odds_work_like_yes_and_no(path, goals):
    ids, event_id = goals
    item = next(s for s in service.page_data(ids["anna"], NOW, path).events
                if s.event.id == event_id)
    assert (item.pool, item.odds_yes, item.odds_no) == (Decimal("40"), 40 / 30, 4.0)


# --- resolving by the actual number -----------------------------------------------

def test_above_the_line_over_wins(path, goals):
    ids, event_id = goals
    assert service.resolve_over_under(ids["ben"], event_id, 3, AFTER, path) == Decimal("40")
    stored = stored_event(path, event_id)
    assert (stored["outcome"], stored["result_value"]) == ("yes", "3")
    assert (balance(path, ids["ben"]), balance(path, ids["carl"])) == (110, 90)


def test_below_the_line_under_wins(path, goals):
    ids, event_id = goals
    service.resolve_over_under(ids["anna"], event_id, Decimal("1"), AFTER, path)
    assert stored_event(path, event_id)["outcome"] == "no"
    assert (balance(path, ids["ben"]), balance(path, ids["carl"])) == (70, 130)


def test_exactly_on_the_line_refunds_everyone(path, group):
    ids = group
    event = service.create_event(ids["ben"], "Goals", CLOSE, NOW, path=path, line=3)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("30"), NOW, path)
    service.place_bet(ids["carl"], event.id, "no", Decimal("10"), NOW, path)
    service.resolve_over_under(ids["ben"], event.id, Decimal("3.00"), AFTER, path)
    stored = stored_event(path, event.id)
    assert (stored["status"], stored["outcome"], stored["result_value"]) == ("resolved", None, "3")
    assert (balance(path, ids["ben"]), balance(path, ids["carl"])) == (100, 100)


def test_winner_label_shows_the_number(path, goals):
    ids, event_id = goals
    service.resolve_over_under(ids["ben"], event_id, 4, AFTER, path)
    event = next(s.event for s in service.page_data(ids["ben"], AFTER, path).events
                 if s.event.id == event_id)
    assert service.winner_label(event) == "OVER 2.5 (4)"


def test_on_the_line_label_says_refunded(path, group):
    ids = group
    event = service.create_event(ids["ben"], "Goals", CLOSE, NOW, path=path, line=3)
    service.resolve_over_under(ids["ben"], event.id, 3, AFTER, path)
    resolved = next(s.event for s in service.page_data(ids["ben"], AFTER, path).events
                    if s.event.id == event.id)
    assert service.winner_label(resolved) == "ON THE LINE (3), REFUNDED"


def test_winner_gets_the_confetti_announcement(path, goals):
    ids, event_id = goals
    service.resolve_over_under(ids["ben"], event_id, 3, AFTER, path)
    assert service.has_new_win(ids["ben"], path) and not service.has_new_win(ids["carl"], path)


def test_cannot_resolve_before_betting_closes(path, goals):
    ids, event_id = goals
    with pytest.raises(BettingError):
        service.resolve_over_under(ids["ben"], event_id, 3, NOW, path)
    assert stored_event(path, event_id)["status"] == "open"


def test_only_creator_or_admin_may_resolve(path, goals):
    ids, event_id = goals
    with pytest.raises(AuthError, match="creator or the group admin"):
        service.resolve_over_under(ids["carl"], event_id, 3, AFTER, path)


def test_outsider_cannot_resolve(path, goals):
    ids, event_id = goals
    with pytest.raises(BettingError):
        service.resolve_over_under(ids["dan"], event_id, 3, AFTER, path)


@pytest.mark.parametrize("actual", [3.0, "3", None, Decimal("3.001")])
def test_bad_result_is_rejected_and_nothing_changes(path, goals, actual):
    ids, event_id = goals
    with pytest.raises(BettingError, match="result"):
        service.resolve_over_under(ids["ben"], event_id, actual, AFTER, path)
    assert stored_event(path, event_id)["status"] != "resolved"


def test_resolved_event_cannot_be_resolved_again(path, goals):
    ids, event_id = goals
    service.resolve_over_under(ids["ben"], event_id, 3, AFTER, path)
    with pytest.raises(BettingError):
        service.resolve_over_under(ids["ben"], event_id, 1, AFTER, path)


def test_yes_no_buttons_do_not_resolve_over_under(path, goals):
    ids, event_id = goals
    with pytest.raises(BettingError, match="actual number"):
        service.resolve_event(ids["ben"], event_id, "yes", AFTER, path)


def test_yes_no_event_is_not_resolved_by_a_number(path, group):
    ids = group
    event = service.create_event(ids["ben"], "Rain", CLOSE, NOW, path=path)
    with pytest.raises(BettingError, match="over/under"):
        service.resolve_over_under(ids["ben"], event.id, 3, AFTER, path)


# --- stored data stays consistent ------------------------------------------------------

def edit_event(path, event_id, **fields):
    """Change fields of a stored event directly and return the data (not saved)."""
    data = storage.load_data(path)
    event = next(e for e in data["events"] if e["id"] == event_id)
    event.update(fields)
    return data


@pytest.mark.parametrize("fields, message", [
    ({"result_value": "3"}, "result_value must be null unless resolved"),
    ({"status": "resolved", "outcome": "yes"}, "result_value must be set when resolved"),
])
def test_validation_ties_the_result_number_to_resolving(path, goals, fields, message):
    ids, event_id = goals
    with pytest.raises(storage.StorageError, match=message):
        storage.validate(edit_event(path, event_id, **fields))


def test_validation_checks_the_outcome_follows_the_number(path, goals):
    ids, event_id = goals
    service.resolve_over_under(ids["ben"], event_id, 3, AFTER, path)
    with pytest.raises(storage.StorageError, match="outcome must be 'yes'"):
        storage.validate(edit_event(path, event_id, outcome="no"))
    with pytest.raises(storage.StorageError, match="outcome must be 'no'"):
        storage.validate(edit_event(path, event_id, result_value="1"))

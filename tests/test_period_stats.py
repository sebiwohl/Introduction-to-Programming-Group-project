"""Tests for "This period" / "All time": which events and bets count in the current period."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, service, stats

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
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


def test_before_any_repayment_everything_is_this_period():
    assert stats.in_period(NOW - timedelta(days=30), None)


@pytest.mark.parametrize("close, counts", [
    (NOW - timedelta(minutes=1), False),
    (NOW, True),
    (NOW + timedelta(days=1), True),
])
def test_an_event_counts_in_the_period_its_betting_closes_in(close, counts):
    assert stats.in_period(close, NOW) is counts


@pytest.fixture
def repaid(path):
    """anna (admin) and ben; event 'old' resolved before Repaid, 'carried' still open,
    'new' created after. Returns (ids, page for ben after Repaid)."""
    ids = {n: auth.sign_up(n, PASSWORD, path).id for n in ("anna", "ben")}
    group = auth.create_group(ids["anna"], "Friends", path=path)
    auth.join_group(ids["ben"], group.invite_code, path, now=NOW)
    start = NOW - timedelta(days=5)
    balances.top_up(ids["anna"], group.id, 100, start, path)
    old = service.create_event(ids["anna"], "Old", start + timedelta(days=1), start, path=path)
    service.place_bet(ids["ben"], old.id, "yes", Decimal("10"), start, path)
    service.place_bet(ids["anna"], old.id, "no", Decimal("10"), start, path)
    service.resolve_event(ids["anna"], old.id, "yes", start + timedelta(days=1), path)
    carried = service.create_event(ids["anna"], "Carried", NOW + timedelta(days=2), start,
                                   path=path)
    service.place_bet(ids["ben"], carried.id, "yes", Decimal("5"), start, path)
    balances.confirm_repaid(ids["anna"], group.id, 50, NOW - timedelta(days=1), path)
    service.create_event(ids["anna"], "New", NOW + timedelta(days=1), NOW, path=path)
    return ids, service.page_data(ids["ben"], NOW, path)


def test_page_knows_when_the_period_started(repaid):
    _, page = repaid
    assert page.period_start == NOW - timedelta(days=1)


def test_this_period_leaves_out_events_closed_before_the_repayment(repaid):
    _, page = repaid
    this = stats.period_events(page.events, stats.THIS_PERIOD, page.period_start)
    every = stats.period_events(page.events, stats.ALL_TIME, page.period_start)
    assert sorted(s.event.title for s in this) == ["Carried", "New"]
    assert sorted(s.event.title for s in every) == ["Carried", "New", "Old"]


def test_bets_follow_their_events_period(repaid):
    _, page = repaid
    this = stats.period_records(page.my_bets, stats.THIS_PERIOD, page.period_start)
    assert [r.event_title for r in this] == ["Carried"]  # carried over at Repaid
    assert len(stats.period_records(page.my_bets, stats.ALL_TIME, page.period_start)) == 2


def test_hit_rate_counts_only_this_periods_results(repaid):
    _, page = repaid
    all_time = stats.member_stats(page.events)["ben"]
    this = stats.member_stats(stats.period_events(page.events, stats.THIS_PERIOD,
                                                  page.period_start)).get("ben")
    assert (all_time.right, all_time.settled) == (1, 1)
    assert this is None  # no settled calls yet this period

"""Tests for stats.countdown: the time left until betting closes on the event card."""

from datetime import datetime, timedelta, timezone

import pytest

from src import stats

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("left, text", [
    (timedelta(days=2, hours=4, minutes=10), "2 d 4 h left"),  # minutes are left out in days
    (timedelta(days=3), "3 d left"),
    (timedelta(days=1, minutes=1), "1 d left"),
    (timedelta(hours=5, minutes=30), "5 h 30 min left"),
    (timedelta(hours=2), "2 h left"),
    (timedelta(minutes=59, seconds=1), "1 h left"),
    (timedelta(minutes=35), "35 min left"),
    (timedelta(seconds=20), "1 min left"),
])
def test_countdown_text(left, text):
    assert stats.countdown(NOW + left, NOW).text == text


def test_red_only_in_the_last_three_hours():
    assert not stats.countdown(NOW + timedelta(hours=3, seconds=1), NOW).urgent
    assert stats.countdown(NOW + timedelta(hours=3), NOW).urgent
    assert stats.countdown(NOW + timedelta(minutes=5), NOW).urgent


@pytest.mark.parametrize("left", [timedelta(0), timedelta(minutes=-10)])
def test_at_or_after_the_close_it_says_closed(left):
    result = stats.countdown(NOW + left, NOW)
    assert (result.text, result.urgent, result.closed) == ("Betting closed", True, True)


def test_works_across_time_zones():
    lisbon = timezone(timedelta(hours=1))
    close = datetime(2026, 11, 20, 14, 0, tzinfo=lisbon)  # 13:00 UTC
    assert stats.countdown(close, NOW).text == "1 h left"

"""Tests for using several groups: the page for a chosen group, and coins staying in their group."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, balances, service, storage
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
def two_groups(path):
    """ben is in Friends (anna's, topped up 100) and Work (carl's, topped up 50); dan only in Work.

    Returns (ids, friends id, work id).
    """
    ids = {name: auth.sign_up(name, PASSWORD, path).id for name in ("anna", "ben", "carl", "dan")}
    friends = auth.create_group(ids["anna"], "Friends", path=path)
    auth.join_group(ids["ben"], friends.invite_code, path, now=NOW)
    balances.top_up(ids["anna"], friends.id, 100, NOW, path)
    work = auth.create_group(ids["carl"], "Work", path=path)
    for name in ("ben", "dan"):
        auth.join_group(ids[name], work.invite_code, path, now=NOW)
    balances.top_up(ids["carl"], work.id, 50, NOW, path)
    return ids, friends.id, work.id


def balances_of(path, user_id):
    """Return {group name: balance} for the user."""
    return {g.name: m.balance for g, m in auth.user_groups(user_id, path)}


# --- the page for a chosen group ------------------------------------------------

def test_page_shows_the_chosen_group(path, two_groups):
    ids, friends, work = two_groups
    page = service.page_data(ids["ben"], NOW, path, group_id=work)
    assert (page.group.id, page.user.balance) == (work, Decimal("50.00"))
    assert {row.username for row in page.leaderboard} == {"ben", "carl", "dan"}
    assert [g.name for g in page.groups] == ["Friends", "Work"]


def test_page_without_a_choice_shows_the_first_group(path, two_groups):
    ids, friends, _ = two_groups
    page = service.page_data(ids["ben"], NOW, path)
    assert (page.group.id, page.user.balance) == (friends, Decimal("100.00"))


def test_a_group_the_user_is_not_in_falls_back_to_their_first(path, two_groups):
    ids, friends, work = two_groups
    page = service.page_data(ids["anna"], NOW, path, group_id=work)
    assert page.group.id == friends
    assert {row.username for row in page.leaderboard} == {"anna", "ben"}


def test_feed_shows_only_the_chosen_groups_events(path, two_groups):
    ids, friends, work = two_groups
    service.create_event(ids["anna"], "Friends event", CLOSE, NOW, path=path)
    service.create_event(ids["carl"], "Work event", CLOSE, NOW, path=path)
    titles = lambda group: [s.event.title for s in  # noqa: E731
                            service.page_data(ids["ben"], NOW, path, group_id=group).events]
    assert (titles(friends), titles(work)) == (["Friends event"], ["Work event"])


# --- creating, betting and resolving in the second group ----------------------------

def test_create_event_in_the_chosen_group(path, two_groups):
    ids, _, work = two_groups
    event = service.create_event(ids["ben"], "Work lunch", CLOSE, NOW, path=path, group_id=work)
    assert event.group_id == work


def test_cannot_create_in_a_group_the_user_is_not_in(path, two_groups):
    ids, _, work = two_groups
    with pytest.raises(BettingError, match="Group not found"):
        service.create_event(ids["anna"], "Sneaky", CLOSE, NOW, path=path, group_id=work)
    assert storage.load_data(path)["events"] == []


def test_bets_and_payouts_stay_in_the_events_group(path, two_groups):
    ids, _, work = two_groups
    event = service.create_event(ids["carl"], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("20"), NOW, path)  # ben's 2nd group
    service.place_bet(ids["dan"], event.id, "no", Decimal("10"), NOW, path)
    assert balances_of(path, ids["ben"]) == {"Friends": Decimal("100.00"), "Work": Decimal("30.00")}
    service.resolve_event(ids["carl"], event.id, "yes", AFTER, path)
    assert balances_of(path, ids["ben"]) == {"Friends": Decimal("100.00"), "Work": Decimal("60.00")}


def test_creator_in_second_group_can_resolve_their_event(path, two_groups):
    ids, _, work = two_groups
    event = service.create_event(ids["ben"], "Ben's work event", CLOSE, NOW, path=path,
                                 group_id=work)
    service.place_bet(ids["dan"], event.id, "no", Decimal("10"), NOW, path)
    service.resolve_event(ids["ben"], event.id, "no", AFTER, path)
    assert balances_of(path, ids["dan"]) == {"Work": Decimal("50.00")}


def test_over_under_in_second_group(path, two_groups):
    ids, _, work = two_groups
    event = service.create_event(ids["ben"], "Goals", CLOSE, NOW, path=path, group_id=work,
                                 line=Decimal("2.5"))
    service.place_bet(ids["ben"], event.id, "yes", Decimal("5"), NOW, path)
    service.place_bet(ids["dan"], event.id, "no", Decimal("5"), NOW, path)
    service.resolve_over_under(ids["ben"], event.id, 3, AFTER, path)
    assert balances_of(path, ids["ben"])["Work"] == Decimal("55.00")


def test_outsider_still_cannot_bet_on_another_groups_event(path, two_groups):
    ids, _, _ = two_groups
    event = service.create_event(ids["carl"], "Rain", CLOSE, NOW, path=path)
    with pytest.raises(BettingError, match="Event not found"):
        service.place_bet(ids["anna"], event.id, "yes", Decimal("5"), NOW, path)


def test_member_names_follow_the_chosen_group(path, two_groups):
    ids, friends, work = two_groups
    assert service.group_member_names(ids["ben"], path, group_id=work) == ["ben", "carl", "dan"]
    assert service.group_member_names(ids["ben"], path, group_id=friends) == ["anna", "ben"]


def test_open_bets_are_counted_per_group(path, two_groups):
    ids, friends, work = two_groups
    event = service.create_event(ids["anna"], "Rain", CLOSE, NOW, path=path)
    service.place_bet(ids["ben"], event.id, "yes", Decimal("5"), NOW, path)
    count = lambda group: next(r.open_bets for r in  # noqa: E731
                               service.page_data(ids["ben"], NOW, path, group_id=group).leaderboard
                               if r.username == "ben")
    assert (count(friends), count(work)) == (1, 0)


def test_bet_records_carry_their_group_so_stats_can_be_per_group(path, two_groups):
    from src import stats
    ids, friends, work = two_groups
    for creator, group in (("anna", friends), ("carl", work)):
        event = service.create_event(ids[creator], f"{creator} event", CLOSE, NOW, path=path)
        service.place_bet(ids["ben"], event.id, "yes", Decimal("5"), NOW, path)
    page = service.page_data(ids["ben"], NOW, path, group_id=work)
    assert sorted(r.group_id for r in page.my_bets) == sorted([friends, work])  # all groups
    in_work = [r for r in page.my_bets if r.group_id == work]
    assert stats.profile_stats(in_work, page.events, ids["ben"]).bets == 1

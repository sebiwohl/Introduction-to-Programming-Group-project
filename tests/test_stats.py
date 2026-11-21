"""Tests for src/stats.py: hit rates, streaks, filters and rankings worked out for display."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import stats
from src.models import Bet, Event
from src.service import BetRecord, Bettor, EventSummary, LeaderboardRow

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def summary(event_id, status, bettors, outcome=None, hours=0):
    """Return an event summary with odds worked out from the bettors."""
    yes = sum((Decimal(s) for _, side, s in bettors if side == "yes"), Decimal("0"))
    no = sum((Decimal(s) for _, side, s in bettors if side == "no"), Decimal("0"))
    pool = yes + no
    event = Event(id=event_id, group_id="g1", creator_id="u1", title=f"Event {event_id}",
                  close_time=NOW + timedelta(hours=hours), status=status, outcome=outcome)
    return EventSummary(
        event=event, pool=pool, stake_yes=yes, stake_no=no,
        odds_yes=float(pool / yes) if yes else None,
        odds_no=float(pool / no) if no else None, my_stake=Decimal("0"),
        bettors=[Bettor(name, side, Decimal(s)) for name, side, s in bettors],
    )


def record(status, stake, payout=None, side="yes", outcome=None):
    """Return one of the user's bet records."""
    bet = Bet(id="b", event_id="e", user_id="u1", side=side, stake=Decimal(stake),
              payout=None if payout is None else Decimal(payout))
    return BetRecord(bet=bet, event_title="Event", event_status=status, outcome=outcome)


def rows(*pairs):
    """Return leaderboard rows from (name, balance) pairs, ranked by balance."""
    ordered = sorted(pairs, key=lambda p: -p[1])
    return [LeaderboardRow(rank=i, username=n, balance=Decimal(b))
            for i, (n, b) in enumerate(ordered, start=1)]


# --- call_result and bet_result ----------------------------------------------

def test_call_result_win_loss_and_refund():
    assert stats.call_result("yes", "yes", Decimal(10), Decimal(5)) == stats.WON
    assert stats.call_result("no", "yes", Decimal(10), Decimal(5)) == stats.LOST
    assert stats.call_result("yes", "yes", Decimal(10), Decimal(0)) == stats.REFUNDED
    assert stats.call_result("yes", "no", Decimal(10), Decimal(0)) == stats.REFUNDED


def test_bet_result_for_each_event_state():
    assert stats.bet_result(record("open", 10)) == stats.OPEN
    assert stats.bet_result(record("paused", 10)) == stats.PAUSED
    assert stats.bet_result(record("closed", 10)) == stats.AWAITING
    assert stats.bet_result(record("deleted", 10)) == stats.REFUNDED
    assert stats.bet_result(record("resolved", 10, "24")) == stats.WON
    assert stats.bet_result(record("resolved", 10, "10")) == stats.REFUNDED
    assert stats.bet_result(record("resolved", 10, "0")) == stats.LOST


# --- member_stats -------------------------------------------------------------

def test_member_stats_hit_rate_best_odds_and_streak():
    events = [
        summary("e1", "resolved", [("Ann", "yes", 10), ("Ben", "no", 30)], "yes", hours=-30),
        summary("e2", "resolved", [("Ann", "yes", 10), ("Ben", "no", 10)], "yes", hours=-20),
        summary("e3", "resolved", [("Ann", "no", 10), ("Ben", "yes", 10)], "yes", hours=-10),
    ]
    result = stats.member_stats(events)
    assert (result["Ann"].right, result["Ann"].settled) == (2, 3)
    assert result["Ann"].best_odds == 4.0
    assert result["Ann"].best_title == "Event e1"
    assert result["Ann"].streak == -1
    assert (result["Ben"].right, result["Ben"].settled) == (1, 3)
    assert result["Ben"].streak == 1
    assert result["Ben"].best_odds == 2.0


def test_member_stats_streak_follows_close_time_not_list_order():
    events = [
        summary("late", "resolved", [("Ann", "yes", 10), ("Ben", "no", 10)], "yes", hours=-1),
        summary("early", "resolved", [("Ann", "no", 10), ("Ben", "yes", 10)], "yes", hours=-9),
    ]
    assert stats.member_stats(events)["Ann"].streak == 1


def test_member_stats_skip_refunds_and_unresolved_events():
    events = [
        summary("e1", "resolved", [("Ann", "yes", 10)], "no"),
        summary("e2", "open", [("Ann", "yes", 10), ("Ben", "no", 10)]),
        summary("e3", "closed", [("Ann", "yes", 10), ("Ben", "no", 10)]),
    ]
    assert stats.member_stats(events) == {}


def test_hit_rate_none_without_settled_calls():
    assert stats.MemberStats().hit_rate is None
    assert stats.MemberStats(right=1, settled=4).hit_rate == 0.25


# --- money helpers ----------------------------------------------------------

def test_estimated_profit_rounds_down():
    s = summary("e", "open", [("Ann", "yes", 30), ("Ben", "no", 70)])
    assert stats.estimated_profit(Decimal("30"), s, "yes") == Decimal("70.00")
    s = summary("e", "open", [("Ann", "yes", 30), ("Ben", "no", 70), ("Cleo", "no", 0)])
    assert stats.estimated_profit(Decimal("10"), s, "no") == Decimal("4.28")


def test_estimated_profit_none_when_side_is_empty():
    s = summary("e", "open", [("Ann", "yes", 30)])
    assert stats.estimated_profit(Decimal("10"), s, "no") is None


def test_in_play_counts_only_unsettled_bets():
    records = [record("open", 10), record("closed", 5), record("paused", 2),
               record("resolved", 50, "0"), record("deleted", 7)]
    assert stats.in_play(records) == Decimal("17")


def test_latest_win_only_if_newest_settled_bet_won():
    assert stats.latest_win([record("closed", 5), record("resolved", 10, "20")]).bet.stake == 10
    assert stats.latest_win([record("resolved", 10, "0"), record("resolved", 10, "20")]) is None
    assert stats.latest_win([record("resolved", 10, "10"), record("resolved", 5, "9")]).bet.stake == 5
    assert stats.latest_win([]) is None


def test_latest_win_counts_only_the_chosen_group():
    """A win in one group is not announced in another group's feed."""
    won, lost = record("resolved", 10, "20"), record("resolved", 10, "0")
    won.group_id, lost.group_id = "g1", "g2"
    assert stats.latest_win([won, lost], "g1") is won
    assert stats.latest_win([won, lost], "g2") is None
    assert stats.latest_win([won], "g3") is None
    assert stats.latest_win([won, lost]) is won  # without a group, all bets count


# --- leaderboard helpers ------------------------------------------------------

def test_next_above_is_the_closest_richer_member():
    board = rows(("Ann", 1060), ("Ben", 980), ("You", 940), ("Cleo", 900))
    assert stats.next_above(board, "You").username == "Ben"
    assert stats.next_above(board, "Ann") is None
    assert stats.next_above(board, "Nobody") is None


def test_closest_rival_is_below_when_leading():
    board = rows(("Ann", 1060), ("Ben", 980), ("You", 940))
    assert stats.closest_rival(board, "You").username == "Ben"
    assert stats.closest_rival(board, "Ann").username == "Ben"
    assert stats.closest_rival(rows(("Ann", 10)), "Ann") is None


def test_rival_choices_list_everyone_else_with_the_closest_rival_first_chosen():
    """The head to head picker offers every other member; the closest rival is preselected."""
    board = rows(("Ann", 1060), ("Ben", 980), ("You", 940), ("Cleo", 900))
    names, index = stats.rival_choices(board, "You")
    assert names == ["Ann", "Ben", "Cleo"]
    assert names[index] == "Ben"
    names, index = stats.rival_choices(board, "Ann")  # leading: the one just below
    assert names[index] == "Ben"


def test_rival_choices_empty_when_alone():
    """With nobody else in the group there is nobody to compare with."""
    assert stats.rival_choices(rows(("Ann", 10)), "Ann") == ([], 0)


def test_rank_members_by_each_ranking():
    board = rows(("Ann", 1000), ("Ben", 900), ("Cleo", 800))
    member = {
        "Ben": stats.MemberStats(right=3, settled=4, best_odds=2.0, streak=3),
        "Cleo": stats.MemberStats(right=1, settled=4, best_odds=5.0, streak=-2),
    }
    names = lambda ranked: [r.username for r in ranked]  # noqa: E731
    assert names(stats.rank_members(board, member, "Coins")) == ["Ann", "Ben", "Cleo"]
    assert names(stats.rank_members(board, member, "Hit rate")) == ["Ben", "Cleo", "Ann"]
    assert names(stats.rank_members(board, member, "Best call")) == ["Cleo", "Ben", "Ann"]
    assert names(stats.rank_members(board, member, "Streak")) == ["Ben", "Cleo", "Ann"]


# --- feed helpers -------------------------------------------------------------

def test_filter_events_per_tab():
    events = [
        summary("soon", "open", [], hours=5),
        summary("later", "open", [], hours=48),
        summary("wait", "closed", [], hours=-1),
        summary("done", "resolved", [], "yes", hours=-5),
    ]
    ids = lambda choice: [s.event.id for s in stats.filter_events(events, choice, NOW)]  # noqa: E731
    assert ids("All") == ["soon", "later", "wait", "done"]
    assert ids("Open") == ["soon", "later"]
    assert ids("Closing soon") == ["soon"]
    assert ids("Awaiting result") == ["wait"]
    assert ids("Done") == ["done"]
    assert ids("Something else") == ids("All")


def test_feed_order_groups_by_status_and_keeps_order_inside():
    events = [
        summary("open1", "open", []), summary("done1", "resolved", [], "yes"),
        summary("wait", "closed", []), summary("open2", "open", []),
        summary("paused", "paused", []), summary("done2", "resolved", [], "no"),
    ]
    assert [s.event.id for s in stats.feed_order(events)] == [
        "open1", "open2", "paused", "wait", "done1", "done2"]


def test_count_by_status():
    events = [summary("a", "open", []), summary("b", "open", []), summary("c", "closed", [])]
    assert stats.count_by_status(events) == {"open": 2, "closed": 1}


def test_nudge_picks_biggest_pool_without_the_user():
    events = [
        summary("small", "open", [("Ann", "yes", 5)]),
        summary("big", "open", [("Ann", "yes", 30), ("Ben", "no", 60), ("Ann", "no", 10)]),
        summary("mine", "open", [("You", "yes", 500)]),
        summary("shut", "closed", [("Ann", "yes", 900)]),
    ]
    result = stats.nudge(events, "You")
    assert result.summary.event.id == "big"
    assert (result.side, result.share, result.bettors) == ("no", 70, 2)


def test_nudge_none_when_user_bet_everywhere_or_no_pool():
    events = [summary("a", "open", [("You", "yes", 5)]), summary("b", "open", [])]
    assert stats.nudge(events, "You") is None


def test_profile_stats_counts_bets_money_and_biggest_win():
    records = [
        record("resolved", 100, payout=250),          # won +150
        record("resolved", 50, payout=80),            # won +30
        record("resolved", 40, payout=0),             # lost -40
        record("resolved", 20, payout=20),            # refunded
        record("deleted", 10),                        # refunded (event deleted)
        record("open", 30),
        record("closed", 5),
    ]
    records[0].event_title = "Big one"
    events = [summary("a", "open", []), summary("b", "closed", [])]
    events[1].event.creator_id = "someone else"
    profile = stats.profile_stats(records, events, "u1")
    assert (profile.bets, profile.won, profile.lost, profile.refunded, profile.unsettled) == (
        7, 2, 1, 2, 2)
    assert profile.total_staked == Decimal("255")
    assert profile.net_won == Decimal("140")
    assert (profile.biggest_win, profile.biggest_win_title) == (Decimal("150"), "Big one")
    assert profile.events_created == 1


def test_profile_stats_empty_user():
    profile = stats.profile_stats([], [], "u1")
    assert (profile.bets, profile.net_won, profile.biggest_win) == (0, Decimal("0"), None)


def test_my_row_finds_user_or_none():
    board = rows(("Ann", 1200), ("You", 900))
    assert stats.my_row(board, "You").rank == 2
    assert stats.my_row(board, "Nobody") is None


def test_hot_bets_need_half_the_group_and_at_least_two_people():
    events = [
        summary("three", "open", [("Ann", "yes", 5), ("Ben", "no", 5), ("Cleo", "yes", 5)]),
        summary("two", "open", [("Ann", "yes", 5), ("Ben", "no", 5)]),
        summary("one_twice", "open", [("Ann", "yes", 5), ("Ann", "no", 5)]),
        summary("closed", "closed", [("Ann", "yes", 5), ("Ben", "no", 5), ("Cleo", "no", 5)]),
    ]
    hot = lambda size: [s.event.id for s in stats.filter_events(events, "Hot bets", NOW, size)]  # noqa: E731
    assert hot(4) == ["three", "two"]          # half of 4 is 2 people
    assert hot(6) == ["three"]                 # half of 6 is 3 people
    assert hot(1) == ["three", "two"]          # never fewer than 2 people
    assert hot(10) == []                       # nobody reaches 5 bettors


def test_hot_bets_most_bettors_first():
    events = [
        summary("two", "open", [("Ann", "yes", 5), ("Ben", "no", 5)]),
        summary("three", "open", [("Ann", "yes", 5), ("Ben", "no", 5), ("Cleo", "yes", 5)]),
    ]
    assert [s.event.id for s in stats.filter_events(events, "Hot bets", NOW, 4)] == ["three", "two"]


def test_bettor_count_counts_people_not_bets():
    event = summary("e", "open", [("Ann", "yes", 5), ("Ann", "no", 5), ("Ben", "yes", 1)])
    assert stats.bettor_count(event) == 2


def test_closing_soon_means_within_12_hours():
    events = [summary("in", "open", [], hours=12), summary("out", "open", [], hours=12.1)]
    soon = [s.event.id for s in stats.filter_events(events, "Closing soon", NOW)]
    assert soon == ["in"]


def test_feed_tabs_open_first_with_paused_and_past_bets():
    events = [summary("open", "open", [], hours=5), summary("paused", "paused", [], hours=5),
              summary("wait", "closed", [], hours=-1), summary("done", "resolved", [], "yes", hours=-5)]
    ids = lambda choice: [s.event.id for s in stats.filter_events(events, choice, NOW)]  # noqa: E731
    assert stats.FEED_FILTERS[0] == "Open" and "All" not in stats.FEED_FILTERS
    assert ids("Open") == ["open", "paused"]  # a paused event's poll still needs votes
    assert ids("Past bets") == ["done"]

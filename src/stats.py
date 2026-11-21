"""Numbers the screens show that are worked out from existing data, never stored.

Pure functions only: no Streamlit, no storage. They take what service.page_data()
returns (event summaries, the user's bet records, leaderboard rows) and compute
hit rates, best calls, streaks, filters and rankings for display.

A "call" is one member's stake on one side of a resolved event. Refunded calls
(only one side had bets) count neither as right nor as wrong. On a names event
a call is a stake on one option; it is refunded when nobody backed a winning
option, or when every stake was on a winning option (everyone got it back).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Dict, List, Optional

from src import betting
from src.models import (
    CENT,
    SIDE_NO,
    SIDE_YES,
    STATUS_CLOSED,
    STATUS_DELETED,
    STATUS_OPEN,
    STATUS_PAUSED,
    STATUS_RESOLVED,
)
from src.service import BetRecord, EventSummary, LeaderboardRow

WON = "won"
LOST = "lost"
REFUNDED = "refunded"
AWAITING = "awaiting"
OPEN = "open"
PAUSED = "paused"

CLOSING_SOON = timedelta(hours=12)  # the "Closing soon" tab (decided: 12 hours)
COUNTDOWN_URGENT = timedelta(hours=3)  # the countdown turns red this close to the close (3.2)
FEED_FILTERS = ("Open", "Hot bets", "Closing soon", "Awaiting result", "Past bets")
# Open is the first tab and the default. "Done" (old name of "Past bets") and "All" still work.
# An open event is "hot" when at least half the group has bet on it (and never fewer than 2 people).
HOT_SHARE = 0.5
HOT_MIN_BETTORS = 2
RANKINGS = ("Coins", "Hit rate", "Best call", "Streak")
UNSETTLED = (STATUS_OPEN, STATUS_PAUSED, STATUS_CLOSED)


@dataclass
class MemberStats:
    """One member's record on resolved events.

    streak is positive for wins in a row and negative for losses in a row.
    """

    right: int = 0
    settled: int = 0
    best_odds: Optional[float] = None
    best_title: str = ""
    streak: int = 0

    @property
    def hit_rate(self) -> Optional[float]:
        """Return the share of settled calls that were right, or None without any."""
        return self.right / self.settled if self.settled else None


def call_result(side: str, outcome: str, stake_yes: Decimal, stake_no: Decimal) -> str:
    """Return WON, LOST or REFUNDED for a stake on `side` once `outcome` is set.

    If only one side had bets, every stake was refunded (betting.resolve_event).
    """
    if stake_yes == 0 or stake_no == 0:
        return REFUNDED
    return WON if side == outcome else LOST


def _is_names(summary: EventSummary) -> bool:
    """Return True if the summary is of a names event."""
    return betting.is_names_event(summary.event)


def _side_stake(summary: EventSummary, side: str) -> Decimal:
    """Return the total stake on one side, or on one option of a names event."""
    if _is_names(summary):
        return next((o.stake for o in summary.options if o.id == side), Decimal("0"))
    return summary.stake_yes if side == SIDE_YES else summary.stake_no


def names_call_result(side: str, summary: EventSummary) -> str:
    """Return WON, LOST or REFUNDED for a stake on one option of a resolved names event.

    Refunded when nobody backed a winning option, or when all the stakes were
    on winning options (then everyone just got their stake back).
    """
    winners = set(summary.event.winners)
    winning = sum((o.stake for o in summary.options if o.id in winners), Decimal("0"))
    if winning == 0 or winning == summary.pool:
        return REFUNDED
    return WON if side in winners else LOST


def bet_result(record: BetRecord) -> str:
    """Return the state of one of the user's bets, as the My bets table shows it.

    OPEN, PAUSED or AWAITING while the event is not settled; WON, LOST or
    REFUNDED once it is (a deleted event refunds every stake). A winner's
    payout is always above the stake, a refund equals it, a loss is zero.
    """
    status = record.event_status
    if status == STATUS_OPEN:
        return OPEN
    if status == STATUS_PAUSED:
        return PAUSED
    if status == STATUS_CLOSED:
        return AWAITING
    if status == STATUS_DELETED or record.bet.payout is None:
        return REFUNDED
    if record.bet.payout > record.bet.stake:
        return WON
    return REFUNDED if record.bet.payout == record.bet.stake else LOST


def _add_call(stats: MemberStats, result: str, odds: Optional[float], title: str) -> None:
    """Update one member's record with one settled call (in close-time order)."""
    stats.settled += 1
    if result == WON:
        stats.right += 1
        stats.streak = stats.streak + 1 if stats.streak > 0 else 1
        if odds is not None and (stats.best_odds is None or odds > stats.best_odds):
            stats.best_odds, stats.best_title = odds, title
    else:
        stats.streak = stats.streak - 1 if stats.streak < 0 else -1


def member_stats(summaries: List[EventSummary]) -> Dict[str, MemberStats]:
    """Return every bettor's hit rate, best winning odds and current streak.

    Resolved events are taken oldest first (by close time), so the streak is
    the run of wins or losses up to the latest event. Members without settled
    calls are missing from the result.
    """
    resolved = sorted(
        (s for s in summaries if s.event.status == STATUS_RESOLVED),
        key=lambda s: s.event.close_time,
    )
    stats: Dict[str, MemberStats] = {}
    for summary in resolved:
        outcome = summary.event.outcome
        if _is_names(summary):
            odds = summary.paid_odds
        else:
            odds = summary.odds_yes if outcome == SIDE_YES else summary.odds_no
        for bettor in summary.bettors:
            if _is_names(summary):
                result = names_call_result(bettor.side, summary)
            else:
                result = call_result(bettor.side, outcome, summary.stake_yes, summary.stake_no)
            if result == REFUNDED:
                continue
            _add_call(stats.setdefault(bettor.username, MemberStats()),
                      result, odds, summary.event.title)
    return stats


def side_odds(summary: EventSummary, side: str) -> Optional[float]:
    """Return the odds of one side of an event (None if nobody backed it).

    On a names event this is the option's odds; for a winning option of a
    resolved event it is what was actually paid (with several winners that
    can be less than the option's own odds).
    """
    if _is_names(summary):
        if summary.event.status == STATUS_RESOLVED and side in summary.event.winners:
            return summary.paid_odds
        return next((o.odds for o in summary.options if o.id == side), None)
    return summary.odds_yes if side == SIDE_YES else summary.odds_no


def estimated_profit(stake: Decimal, summary: EventSummary, side: str) -> Optional[Decimal]:
    """Return what a stake on `side` would win on top of itself at today's odds.

    stake x pool / stake on that side, minus the stake, rounded down to the cent.
    None if nobody backed that side. Only an estimate while betting is open
    (on a names event where several can win, it is the most it could win).
    """
    side_stake = _side_stake(summary, side)
    if side_stake == 0:
        return None
    payout = stake * summary.pool / side_stake
    return (payout - stake).quantize(CENT, rounding=ROUND_DOWN)


def payout_preview(stake: Decimal, summary: EventSummary, side: str) -> Optional[Decimal]:
    """Return roughly what a new stake on `side` would pay back if that side wins.

    The new stake is counted in the pool and on its side: stake x (pool +
    stake) / (stake on that side + stake), rounded down to the cent. On a
    names event where several can win it is the most it could pay. None for
    a stake that is not positive.
    """
    if stake <= 0:
        return None
    payout = stake * (summary.pool + stake) / (_side_stake(summary, side) + stake)
    return payout.quantize(CENT, rounding=ROUND_DOWN)


def in_play(records: List[BetRecord]) -> Decimal:
    """Return the user's total stake on events that are not settled yet."""
    return sum(
        (r.bet.stake for r in records if r.event_status in UNSETTLED), Decimal("0")
    )


def latest_win(records: List[BetRecord], group_id: Optional[str] = None) -> Optional[BetRecord]:
    """Return the user's newest settled bet if it was a win, else None.

    Records come newest event first (service._bet_history); refunds are skipped.
    With group_id, only bets in that group count, so a win in one group is not
    announced in another.
    """
    for record in records:
        if group_id is not None and record.group_id != group_id:
            continue
        result = bet_result(record)
        if result in (WON, LOST):
            return record if result == WON else None
    return None


def next_above(rows: List[LeaderboardRow], username: str) -> Optional[LeaderboardRow]:
    """Return the member with the smallest balance above the user's, or None if leading."""
    mine = next((r for r in rows if r.username == username), None)
    if mine is None:
        return None
    above = [r for r in rows if r.balance > mine.balance]
    return min(above, key=lambda r: r.balance) if above else None


def closest_rival(rows: List[LeaderboardRow], username: str) -> Optional[LeaderboardRow]:
    """Return the member just above the user, or just below if the user leads."""
    above = next_above(rows, username)
    if above is not None:
        return above
    mine = next((r for r in rows if r.username == username), None)
    others = [r for r in rows if r.username != username]
    if mine is None or not others:
        return None
    return max(others, key=lambda r: r.balance)


def bettor_count(summary: EventSummary) -> int:
    """Return how many different members have bet on the event."""
    return len({b.username for b in summary.bettors})


def is_hot(summary: EventSummary, group_size: int) -> bool:
    """Return True for an open event that at least half the group (and 2+ people) bet on."""
    needed = max(HOT_MIN_BETTORS, math.ceil(group_size * HOT_SHARE))
    return summary.event.status == STATUS_OPEN and bettor_count(summary) >= needed


def filter_events(summaries: List[EventSummary], choice: str, now: datetime,
                  group_size: int = 0) -> List[EventSummary]:
    """Return the events for one feed tab (see FEED_FILTERS); unknown choices show all.

    group_size is the number of members, used to decide which events are hot.
    Hot bets come with the most bettors first.
    """
    if choice == "Hot bets":
        hot = [s for s in summaries if is_hot(s, group_size)]
        return sorted(hot, key=bettor_count, reverse=True)

    def keep(summary: EventSummary) -> bool:
        """Return True if the event belongs in the chosen tab."""
        status = summary.event.status
        if choice == "Open":  # paused events too: their poll is waiting for votes
            return status in (STATUS_OPEN, STATUS_PAUSED)
        if choice == "Closing soon":
            return status == STATUS_OPEN and summary.event.close_time - now <= CLOSING_SOON
        if choice == "Awaiting result":
            return status == STATUS_CLOSED
        if choice in ("Past bets", "Done"):
            return status == STATUS_RESOLVED
        return True
    return [s for s in summaries if keep(s)]


FEED_ORDER = {STATUS_OPEN: 0, STATUS_PAUSED: 1, STATUS_CLOSED: 2, STATUS_RESOLVED: 3}


def feed_order(summaries: List[EventSummary]) -> List[EventSummary]:
    """Return open events first, then paused, then waiting for a result, then done.

    The sort is stable, so the service's order inside each group is kept
    (open: soonest close first; the rest: newest first).
    """
    return sorted(summaries, key=lambda s: FEED_ORDER.get(s.event.status, len(FEED_ORDER)))


def count_by_status(summaries: List[EventSummary]) -> Dict[str, int]:
    """Return how many events are in each status, e.g. {'open': 2, 'closed': 1}."""
    counts: Dict[str, int] = {}
    for summary in summaries:
        counts[summary.event.status] = counts.get(summary.event.status, 0) + 1
    return counts


def rank_members(
    rows: List[LeaderboardRow], stats: Dict[str, MemberStats], ranking: str
) -> List[LeaderboardRow]:
    """Return the rows sorted by one of RANKINGS, best first; ties keep coin order.

    Members without settled calls go last for hit rate, best call and streak.
    """
    def value(row: LeaderboardRow):
        """Return the sort value of one row (higher is better)."""
        member = stats.get(row.username, MemberStats())
        if ranking == "Hit rate":
            return member.hit_rate if member.hit_rate is not None else -1.0
        if ranking == "Best call":
            return member.best_odds if member.best_odds is not None else -1.0
        if ranking == "Streak":
            return member.streak if member.settled else float("-inf")
        return row.balance
    return sorted(rows, key=value, reverse=True)


@dataclass
class Nudge:
    """An open event the user has not bet on yet, and where the pool leans.

    label is how the leading side is shown: YES, NO or an option's name.
    """

    summary: EventSummary
    side: str
    share: int
    bettors: int
    label: str = ""


def nudge(summaries: List[EventSummary], username: str) -> Optional[Nudge]:
    """Return the open event with the biggest pool that the user has not bet on.

    share is the percentage of the pool on the leading side (YES on a tie; on
    a names event the option with the most coins, the first listed on a tie);
    bettors is how many members have bet on it. None if there is no such event.
    """
    candidates = [
        s for s in summaries
        if s.event.status == STATUS_OPEN and s.pool > 0
        and all(b.username != username for b in s.bettors)
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda s: s.pool)
    names = {b.username for b in best.bettors}
    if _is_names(best):
        top = max(best.options, key=lambda o: o.stake)
        return Nudge(summary=best, side=top.id, share=round(top.stake / best.pool * 100),
                     bettors=len(names), label=top.name)
    side = SIDE_YES if best.stake_yes >= best.stake_no else SIDE_NO
    lead = best.stake_yes if side == SIDE_YES else best.stake_no
    return Nudge(summary=best, side=side, share=round(lead / best.pool * 100),
                 bettors=len(names), label=betting.side_label(best.event, side))


@dataclass
class ProfileStats:
    """Counts and totals of one user's own bets, for the Profile page.

    unsettled counts bets on open, paused or closed events. total_staked is every
    stake ever placed; net_won is payouts minus stakes on settled bets only.
    """

    bets: int = 0
    won: int = 0
    lost: int = 0
    refunded: int = 0
    unsettled: int = 0
    total_staked: Decimal = Decimal("0")
    net_won: Decimal = Decimal("0")
    biggest_win: Optional[Decimal] = None
    biggest_win_title: str = ""
    events_created: int = 0


def _add_record(profile: ProfileStats, record: BetRecord) -> None:
    """Update the profile totals with one of the user's bets."""
    bet = record.bet
    result = bet_result(record)
    profile.bets += 1
    profile.total_staked += bet.stake
    if result not in (WON, LOST, REFUNDED):
        profile.unsettled += 1
        return
    paid = bet.payout if bet.payout is not None else bet.stake
    profile.net_won += paid - bet.stake
    if result == WON:
        profile.won += 1
        gain = paid - bet.stake
        if profile.biggest_win is None or gain > profile.biggest_win:
            profile.biggest_win, profile.biggest_win_title = gain, record.event_title
    elif result == LOST:
        profile.lost += 1
    else:
        profile.refunded += 1


def profile_stats(records: List[BetRecord], summaries: List[EventSummary],
                  user_id: str) -> ProfileStats:
    """Return the user's bet counts, money totals, biggest win and events created.

    records are the user's own bets (service._bet_history); summaries are the
    group's visible events, used to count the ones the user created.
    """
    profile = ProfileStats()
    for record in records:
        _add_record(profile, record)
    profile.events_created = sum(1 for s in summaries if s.event.creator_id == user_id)
    return profile


def my_row(rows: List[LeaderboardRow], username: str) -> Optional[LeaderboardRow]:
    """Return the user's own leaderboard row, or None if they are not on it."""
    return next((r for r in rows if r.username == username), None)


@dataclass
class Countdown:
    """The time left until betting closes, as the event card shows it."""

    text: str
    urgent: bool  # red: less than COUNTDOWN_URGENT left
    closed: bool  # the close time has passed (the event closes at the next page load)


def countdown(close_time: datetime, now: datetime) -> Countdown:
    """Return the time left until close_time, like '2 d 4 h left' or '35 min left'.

    Whole minutes are rounded up, so '1 min left' shows until the close. Less
    than a day shows hours and minutes; a day or more shows days and hours.
    """
    left = close_time - now
    if left <= timedelta(0):
        return Countdown("Betting closed", urgent=True, closed=True)
    minutes = math.ceil(left.total_seconds() / 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        text = f"{days} d {hours} h left" if hours else f"{days} d left"
    elif hours:
        text = f"{hours} h {mins} min left" if mins else f"{hours} h left"
    else:
        text = f"{mins} min left"
    return Countdown(text, urgent=left <= COUNTDOWN_URGENT, closed=False)


# --- Since last repayment / All time ------------------------------------------------------

THIS_PERIOD = "Since last repayment"
ALL_TIME = "All time"
PERIODS = (THIS_PERIOD, ALL_TIME)


def in_period(close_time: Optional[datetime], period_start: Optional[datetime]) -> bool:
    """Return True if an event closing at close_time counts in the current period.

    An event counts in the period in which its betting closes (open bets carry
    over at a repayment, and so do their events). Before the first repayment
    (period_start None) everything is in the period.
    """
    return period_start is None or close_time is None or close_time >= period_start


def period_events(summaries: List[EventSummary], period: str,
                  period_start: Optional[datetime]) -> List[EventSummary]:
    """Return the events counted for the chosen period (all of them for All time)."""
    if period == ALL_TIME:
        return summaries
    return [s for s in summaries if in_period(s.event.close_time, period_start)]


def period_records(records: List[BetRecord], period: str,
                   period_start: Optional[datetime]) -> List[BetRecord]:
    """Return the bets counted for the chosen period (all of them for All time)."""
    if period == ALL_TIME:
        return records
    return [r for r in records if in_period(r.close_time, period_start)]

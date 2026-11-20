"""Service layer: what the UI calls. Connects storage, conversion and betting.

Every change runs as "load fresh data, check, change, save" inside one
storage.transaction(), so a user's balance and their bet are always saved
together. Read-only functions use storage.load_data().

All rule violations raise BettingError with a message the UI can show.
Times passed in (`now`, `close_time`, `event_time`) must be timezone-aware.

Permissions come from auth.require_permission(). Accounts and groups live in
auth.py; deleting events, flags and polls in moderation.py (refresh() ends due polls).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from src import auth, balances, betting, moderation, notifications, storage
from src.betting import BettingError
from src.convert import (
    bet_from_dict,
    bet_to_dict,
    event_from_dict,
    event_to_dict,
    find_membership,
    group_from_dict,
    group_to_dict,
    notification_to_dict,
    poll_from_dict,
    poll_to_dict,
    user_from_dict,
    user_to_dict,
    money_to_cents,
    text_to_time,
)
from src.models import (
    AVATARS,
    TYPE_NAMES,
    TYPE_OVER_UNDER,
    TYPE_YES_NO,
    SIDE_NO,
    SIDE_YES,
    STATUS_CLOSED,
    STATUS_DELETED,
    STATUS_OPEN,
    STATUS_PAUSED,
    STATUS_RESOLVED,
    Bet,
    Event,
    Group,
    Notification,
    Poll,
    User,
)

MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500


# --- results returned to the UI ---------------------------------------------

@dataclass
class Bettor:
    """One member's total stake on one side of an event (all their bets added up)."""

    username: str
    side: str
    stake: Decimal


@dataclass
class OptionRow:
    """One option of a names event with its live numbers.

    odds is pool / stake on this option (None while nobody backed it); share is
    the part of the pool on it, 0 to 1 (None while the pool is empty).
    """

    id: str
    name: str
    stake: Decimal
    odds: Optional[float]
    share: Optional[float]
    my_stake: Decimal
    won: bool = False


@dataclass
class EventSummary:
    """An event with the numbers the feed shows.

    creator_name and bettors are read-only extras for the feed: who created the
    event and who backed which side (largest stake first). A names event has
    one OptionRow per option instead of the yes/no numbers (those stay 0 and
    None), and once resolved, paid_odds is what each coin on a winning option
    paid (None if every stake was refunded). poll is the event's running poll
    (only while the event is paused), else None.
    """

    event: Event
    pool: Decimal
    stake_yes: Decimal
    stake_no: Decimal
    odds_yes: Optional[float]
    odds_no: Optional[float]
    my_stake: Decimal
    creator_name: str = ""
    bettors: List[Bettor] = field(default_factory=list)
    options: List[OptionRow] = field(default_factory=list)
    paid_odds: Optional[float] = None
    poll: Optional[Poll] = None


@dataclass
class BetRecord:
    """One past or open bet of a user, with its event's title and result.

    event_type is the event's type; side_label is how the bet's side is shown
    (YES, NO or an option's name); winner_label names the result once resolved
    ('YES', or 'Marta and João'). group_id is the event's group, as a user's
    bets come from all their groups.
    """

    bet: Bet
    event_title: str
    event_status: str
    outcome: Optional[str]
    side_label: str = ""
    winner_label: str = ""
    event_type: str = TYPE_YES_NO
    group_id: str = ""
    close_time: Optional[datetime] = None  # decides the period the bet counts in


@dataclass
class LeaderboardRow:
    """One member's place on the leaderboard.

    open_bets counts the member's bets that are not settled yet (event open,
    paused or waiting for its result). result is their result this period:
    balance + open stakes - received (see balances.period_result).
    """

    rank: int
    username: str
    balance: Decimal
    is_admin: bool = False
    open_bets: int = 0
    avatar: str = ""
    received: Decimal = Decimal("0")
    result: Decimal = Decimal("0")


# --- loading and saving -------------------------------------------------------

@dataclass
class _State:
    """All records as dataclasses, keyed by id where lookups are needed."""

    users: Dict[str, User]
    groups: Dict[str, Group]
    events: Dict[str, Event]
    bets: List[Bet]
    polls: List[Poll]


def _membership_in(memberships: List[dict], user_id: str, group_id: Optional[str]):
    """Return the user's membership in group_id, else their first one (or None)."""
    if group_id is not None:
        for membership in memberships:
            if membership["user_id"] == user_id and membership["group_id"] == group_id:
                return membership
    return find_membership(memberships, user_id)


def _member_of(data: dict, user_id: str, group_id: Optional[str]) -> bool:
    """Return True if the user has a membership in the group."""
    return any(m["user_id"] == user_id and m["group_id"] == group_id for m in data["memberships"])


def _event_group(data: dict, event_id: str) -> Optional[str]:
    """Return the group of a stored event, or None if there is no such event."""
    return next((e["group_id"] for e in data["events"] if e["id"] == event_id), None)


def _unpack(data: dict, group_id: Optional[str] = None) -> _State:
    """Convert the stored dicts into dataclasses.

    Each user gets the group, role and balance of their membership in group_id
    (a user can be in several groups), or of their first membership if they are
    not in it. Actions on an event pass the event's group, pages the chosen one,
    so balances always change in the right group.
    """
    memberships = data["memberships"]
    return _State(
        users={u["id"]: user_from_dict(u, _membership_in(memberships, u["id"], group_id))
               for u in data["users"]},
        groups={g["id"]: group_from_dict(g) for g in data["groups"]},
        events={e["id"]: event_from_dict(e) for e in data["events"]},
        bets=[bet_from_dict(b) for b in data["bets"]],
        polls=[poll_from_dict(p) for p in data["polls"]],
    )


def _pack_balances(state: _State, data: dict) -> None:
    """Write each user's role and balance back into their membership in user.group_id.

    That is the group _unpack gave them, so a change made in a user's second
    group lands in that group, never in their first one.
    """
    for user in state.users.values():
        membership = _membership_in(data["memberships"], user.id, user.group_id)
        if membership is not None and membership["group_id"] == user.group_id:
            membership["role"] = user.role
            membership["balance"] = money_to_cents(user.balance)


def _pack(state: _State, data: dict) -> None:
    """Write the dataclasses back into the stored dicts (in place)."""
    data["users"] = [user_to_dict(u) for u in state.users.values()]
    _pack_balances(state, data)
    data["groups"] = [group_to_dict(g) for g in state.groups.values()]
    data["events"] = [event_to_dict(e) for e in state.events.values()]
    data["bets"] = [bet_to_dict(b) for b in state.bets]
    data["polls"] = [poll_to_dict(p) for p in state.polls]


def _load(path, group_id: Optional[str] = None) -> _State:
    """Load the data read-only and convert it (see _unpack for group_id)."""
    return _unpack(storage.load_data(path), group_id)


# --- checks -------------------------------------------------------------------

def _require_aware(moment: datetime, name: str) -> None:
    """Raise BettingError unless `moment` is a timezone-aware datetime."""
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise BettingError(f"{name} must be a timezone-aware date and time.")


def _get_user(state: _State, user_id: str) -> User:
    """Return the user, or raise BettingError if they do not exist."""
    user = state.users.get(user_id)
    if user is None:
        raise BettingError("Unknown user.")
    return user


def _get_event_for(state: _State, user: User, event_id: str) -> Event:
    """Return the event if it exists and is in one of the user's groups.

    A missing event and another group's event give the same message, so users
    cannot find out which ids exist in other groups.
    """
    event = state.events.get(event_id)
    if event is None or not betting.can_access_event(user, event):
        raise BettingError("Event not found.")
    return event


def _clean_text(text: str, name: str, max_length: int, required: bool) -> str:
    """Return text with surrounding spaces removed, or raise BettingError."""
    if not isinstance(text, str):
        raise BettingError(f"{name} must be text.")
    text = text.strip()
    if required and not text:
        raise BettingError(f"{name} cannot be empty.")
    if len(text) > max_length:
        raise BettingError(f"{name} can be at most {max_length} characters.")
    return text


def _bets_on(state: _State, event_id: str) -> List[Bet]:
    """Return all bets on one event."""
    return [bet for bet in state.bets if bet.event_id == event_id]


def _close_expired(state: _State, now: datetime) -> int:
    """Mark open events past their close time as closed; return how many."""
    closed = 0
    for event in state.events.values():
        if event.status == STATUS_OPEN and now >= event.close_time:
            event.status = STATUS_CLOSED
            closed += 1
    return closed


def _username(state: _State, user_id: str) -> str:
    """Return a user's name, or a placeholder if the account no longer exists."""
    user = state.users.get(user_id)
    return user.username if user else "Unknown"


def _bettors(state: _State, bets: List[Bet]) -> List[Bettor]:
    """Add up each member's stake per side; largest stake first, then by name."""
    totals: Dict[tuple, Decimal] = {}
    for bet in bets:
        key = (bet.user_id, bet.side)
        totals[key] = totals.get(key, Decimal("0")) + bet.stake
    bettors = [
        Bettor(username=_username(state, user_id), side=side, stake=stake)
        for (user_id, side), stake in totals.items()
    ]
    bettors.sort(key=lambda b: (-b.stake, b.username.casefold()))
    return bettors


def _option_rows(event: Event, bets: List[Bet], user: User, total: Decimal) -> List[OptionRow]:
    """Return a names event's options with stake, odds, pool share and the user's stake."""
    stakes = betting.stake_per_option(bets, event)
    mine = {option_id: Decimal("0") for option_id in stakes}
    for bet in bets:
        if bet.user_id == user.id:
            mine[bet.side] += bet.stake
    return [
        OptionRow(id=option.id, name=option.name, stake=stakes[option.id],
                  odds=betting.odds_for(total, stakes[option.id]),
                  share=betting.share_for(total, stakes[option.id]),
                  my_stake=mine[option.id], won=option.id in event.winners)
        for option in event.options
    ]


def _summarize_names(state: _State, event: Event, user: User, bets: List[Bet]) -> EventSummary:
    """Return the feed numbers for a names event: one row per option."""
    total = betting.pool(bets)
    rows = _option_rows(event, bets, user, total)
    paid = betting.paid_odds(bets, event.winners) if event.status == STATUS_RESOLVED else None
    return EventSummary(
        event=event, pool=total, stake_yes=Decimal("0"), stake_no=Decimal("0"),
        odds_yes=None, odds_no=None, my_stake=sum((row.my_stake for row in rows), Decimal("0")),
        creator_name=_username(state, event.creator_id), bettors=_bettors(state, bets),
        options=rows, paid_odds=paid,
    )


def _summarize(state: _State, event: Event, user: User) -> EventSummary:
    """Return the feed numbers for one event."""
    bets = _bets_on(state, event.id)
    if betting.is_names_event(event):
        return _summarize_names(state, event, user, bets)
    stakes = betting.stake_per_side(bets)
    my_stake = sum((b.stake for b in bets if b.user_id == user.id), Decimal("0"))
    return EventSummary(
        event=event,
        pool=betting.pool(bets),
        stake_yes=stakes[SIDE_YES],
        stake_no=stakes[SIDE_NO],
        odds_yes=betting.odds(bets, SIDE_YES),
        odds_no=betting.odds(bets, SIDE_NO),
        my_stake=my_stake,
        creator_name=_username(state, event.creator_id),
        bettors=_bettors(state, bets),
    )


def _feed(state: _State, user: User) -> List[EventSummary]:
    """Return the user's visible events, open first (soonest close), then the rest."""
    events = [
        e for e in betting.visible_events(user, list(state.events.values()))
        if e.status != STATUS_DELETED
    ]
    open_events = sorted(
        (e for e in events if e.status == STATUS_OPEN), key=lambda e: e.close_time
    )
    other_events = sorted(
        (e for e in events if e.status != STATUS_OPEN),
        key=lambda e: e.close_time, reverse=True,
    )
    running = {poll.event_id: poll for poll in state.polls if poll.result is None}
    summaries = [_summarize(state, e, user) for e in open_events + other_events]
    for summary in summaries:
        summary.poll = running.get(summary.event.id)
    return summaries


def _bet_history(state: _State, user: User) -> List[BetRecord]:
    """Return the user's bets with their events' titles and results, newest event first."""
    records, winners = [], {}
    for bet in state.bets:
        if bet.user_id != user.id:
            continue
        event = state.events[bet.event_id]
        if event.id not in winners:
            winners[event.id] = winner_label(event)
        records.append(BetRecord(
            bet=bet, event_title=event.title,
            event_status=event.status, outcome=event.outcome,
            side_label=betting.side_label(event, bet.side),
            winner_label=winners[event.id], event_type=event.event_type,
            group_id=event.group_id, close_time=event.close_time,
        ))
    records.sort(key=lambda r: state.events[r.bet.event_id].close_time, reverse=True)
    return records


def join_names(names: List[str]) -> str:
    """Return names as one phrase: 'Marta', 'Marta and João', 'Ana, Marta and João'."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def winner_label(event: Event) -> str:
    """Return the result of a resolved event as text, like 'YES' or 'Marta and João'."""
    if event.status != STATUS_RESOLVED:
        return ""
    if betting.is_over_under(event):
        if event.outcome is None:
            return f"ON THE LINE ({event.result_value}), REFUNDED"
        return f"{betting.side_label(event, event.outcome)} ({event.result_value})"
    if not betting.is_names_event(event):
        return (event.outcome or "").upper()
    return join_names([betting.side_label(event, option_id) for option_id in event.winners])


def _leaderboard(state: _State, user: User) -> List[LeaderboardRow]:
    """Rank the members of the user's group by balance; equal balances share a rank."""
    if not user.group_id:
        return []
    members = [u for u in state.users.values() if u.group_id == user.group_id]
    members.sort(key=lambda u: (-u.balance, u.username.casefold()))
    open_bets = _open_bet_counts(state, user.group_id)
    stakes = _open_stake_totals(state, user.group_id)
    rows, rank, previous = [], 0, None
    for position, member in enumerate(members, start=1):
        if member.balance != previous:
            rank, previous = position, member.balance
        rows.append(LeaderboardRow(
            rank=rank, username=member.username, balance=member.balance,
            is_admin=auth.is_group_admin(member, user.group_id),
            open_bets=open_bets.get(member.id, 0), avatar=member.avatar,
            received=member.received,
            result=balances.period_result(member, stakes.get(member.id, Decimal("0"))),
        ))
    return rows


def _open_stake_totals(state: _State, group_id: str) -> Dict[str, Decimal]:
    """Return each user's stakes on the group's bets that are not settled yet."""
    unsettled = (STATUS_OPEN, STATUS_PAUSED, STATUS_CLOSED)
    totals: Dict[str, Decimal] = {}
    for bet in state.bets:
        event = state.events.get(bet.event_id)
        if (event is not None and event.group_id == group_id and event.status in unsettled
                and bet.payout is None):
            totals[bet.user_id] = totals.get(bet.user_id, Decimal("0")) + bet.stake
    return totals


def _open_bet_counts(state: _State, group_id: str) -> Dict[str, int]:
    """Count each user's bets in the group that are not settled yet (open, paused or closed)."""
    unsettled = (STATUS_OPEN, STATUS_PAUSED, STATUS_CLOSED)
    counts: Dict[str, int] = {}
    for bet in state.bets:
        event = state.events.get(bet.event_id)
        if (event is not None and event.group_id == group_id and event.status in unsettled
                and bet.payout is None):
            counts[bet.user_id] = counts.get(bet.user_id, 0) + 1
    return counts


# --- public functions -------------------------------------------------------

def _needs_refresh(state: _State, now: datetime) -> bool:
    """Return True if a poll has ended or an open event is past its close time."""
    return moderation.polls_due(state.polls, now) or _close_expired(state, now) > 0


def refresh(now: datetime, path=None) -> int:
    """End due polls, then close every open event past its close time.

    Returns how many events were closed. Call when a page opens. Saves only if
    something changed, so viewing pages does not create a new backup each time.
    """
    _require_aware(now, "now")
    if not _needs_refresh(_load(path), now):
        return 0
    with storage.transaction(path) as data:
        moderation.end_due_polls(data, now)
        state = _unpack(data)
        closed = _close_expired(state, now)
        _pack(state, data)
    return closed


def get_user(user_id: str, path=None) -> User:
    """Return a user, for example to show their balance."""
    return _get_user(_load(path), user_id)


@dataclass
class NewWin:
    """A win the user has not celebrated yet: its message id, the event and the coins won."""

    notification_id: str
    event_title: str
    amount: Decimal


@dataclass
class PageData:
    """Everything the main screens show, read from one load of the data.

    new_wins are the user's wins announced since they last saw them (for the confetti).
    notifications are the user's own notifications, newest first (for the bell).
    bet_events has the user's bets' events from all their groups (for My bets).
    period_start is when the group's current period began (its last repayment),
    or None before the first one; stats for "This period" count events whose
    betting closes from then on.
    """

    user: User
    group: Optional[Group]
    events: List[EventSummary]
    my_bets: List[BetRecord]
    leaderboard: List[LeaderboardRow]
    new_wins: List[NewWin] = field(default_factory=list)
    notifications: List[Notification] = field(default_factory=list)
    groups: List[Group] = field(default_factory=list)
    period_start: Optional[datetime] = None
    bet_events: Dict[str, EventSummary] = field(default_factory=dict)


def _bet_events(state: _State, user: User) -> Dict[str, EventSummary]:
    """Return {event id: summary} for every event the user bet on, in any of their groups."""
    ids = {bet.event_id for bet in state.bets if bet.user_id == user.id}
    return {event_id: _summarize(state, state.events[event_id], user)
            for event_id in ids if event_id in state.events}


def _period_start(data: dict, group_id: Optional[str]) -> Optional[datetime]:
    """Return when the group's last repayment was confirmed, or None if there was none."""
    times = [text_to_time(r["created_at"]) for r in data["repayments"] if r["group_id"] == group_id]
    return max(times, default=None)


def page_data(user_id: str, now: datetime, path=None,
              group_id: Optional[str] = None) -> PageData:
    """Return the user, group, feed, own bets and leaderboard from a single load.

    The UI calls this once per page view instead of five separate functions, so
    the database is read once per click. If a poll has ended or an open event is
    past its close time, that is saved first (rare), and the data is read again.
    group_id is the group the user chose; if they are not in it (any more),
    their first group is shown. groups lists all their groups for the switcher.
    """
    _require_aware(now, "now")
    data = storage.load_data(path)
    chosen = group_id if _member_of(data, user_id, group_id) else None
    state = _unpack(data, chosen)
    if _needs_refresh(state, now):
        refresh(now, path)
        data = storage.load_data(path)
        state = _unpack(data, chosen)
    user = _get_user(state, user_id)
    mine = {m["group_id"] for m in data["memberships"] if m["user_id"] == user_id}
    return PageData(
        user=user,
        groups=sorted((g for g in state.groups.values() if g.id in mine),
                      key=lambda g: (g.name.casefold(), g.id)),
        group=state.groups.get(user.group_id) if user.group_id else None,
        events=_feed(state, user),
        my_bets=_bet_history(state, user),
        leaderboard=_leaderboard(state, user),
        new_wins=_new_wins(data, state, user.id),
        notifications=notifications.user_notifications(data, user.id),
        period_start=_period_start(data, user.group_id),
        bet_events=_bet_events(state, user),
    )


def get_group(user_id: str, path=None) -> Optional[Group]:
    """Return the user's own group (name, invite code), or None if they have none."""
    state = _load(path)
    user = _get_user(state, user_id)
    if not user.group_id:
        return None
    return state.groups.get(user.group_id)


def create_event(
    user_id: str,
    title: str,
    close_time: datetime,
    now: datetime,
    description: str = "",
    path=None,
    options: Optional[List[str]] = None,
    multi_winner: bool = False,
    line=None,
    event_time: Optional[datetime] = None,
    group_id: Optional[str] = None,
) -> Event:
    """Create an event in the user's group and return it.

    group_id picks one of the user's groups (a user can be in several);
    without it, their first group. A group they are not in is not found.

    Without options or line it is a yes/no event. With a line (a number with at
    most 2 decimals) it is an over/under event: members bet over or under it,
    and the line cannot change. With options it is a names event:
    2 to 10 names (trimmed, not empty, at most 40 characters, no duplicates
    ignoring case), and multi_winner says whether several can win. The names
    cannot change afterwards. The title is required, both texts are trimmed
    and length-limited, and the close time must be in the future.

    event_time is when the event happens (betting.close_time_for works out the
    close time from it). Betting must close no later than that, and the result
    can only be set after it. Without it, the event happens when betting closes.
    """
    _require_aware(now, "now")
    _require_aware(close_time, "Close time")
    if event_time is None:
        event_time = close_time
    _require_aware(event_time, "Event date")
    title = _clean_text(title, "Title", MAX_TITLE_LENGTH, required=True)
    description = _clean_text(
        description, "Description", MAX_DESCRIPTION_LENGTH, required=False
    )
    if close_time > event_time:
        raise BettingError(betting.CLOSE_AFTER_EVENT)
    if close_time <= now:
        raise BettingError("Betting would already be closed: it must close in the future. "
                           "Pick a later event date or another option.")
    if not isinstance(multi_winner, bool):
        raise BettingError("Choose one winner or several winners.")
    if multi_winner and options is None:
        raise BettingError("Several winners are only possible on a names event.")
    if options is not None and line is not None:
        raise BettingError("Choose names or over/under, not both.")
    names = {}
    if options is not None:
        names = {"event_type": TYPE_NAMES, "options": betting.make_options(options),
                 "multi_winner": multi_winner}
    if line is not None:
        names = {"event_type": TYPE_OVER_UNDER, "line": betting.clean_number(line, "line")}
    with storage.transaction(path) as data:
        if group_id is not None and not _member_of(data, user_id, group_id):
            raise BettingError("Group not found.")
        state = _unpack(data, group_id)
        user = _get_user(state, user_id)
        auth.require_permission(user, auth.CREATE_EVENT)
        event = Event(
            id=uuid.uuid4().hex, group_id=user.group_id, creator_id=user.id,
            title=title, description=description, close_time=close_time,
            event_time=event_time, **names,
        )
        state.events[event.id] = event
        _pack(state, data)
    return event


def feed(user_id: str, now: datetime, path=None) -> List[EventSummary]:
    """Return the events in the user's groups with pool and odds.

    Deleted events are hidden. Open events come first, soonest close time
    first, then the rest with the newest close time first.
    """
    refresh(now, path)
    state = _load(path)
    return _feed(state, _get_user(state, user_id))


def event_details(user_id: str, event_id: str, now: datetime, path=None) -> EventSummary:
    """Return one event with its numbers, if the user may see it."""
    refresh(now, path)
    data = storage.load_data(path)
    state = _unpack(data, _event_group(data, event_id))
    user = _get_user(state, user_id)
    event = _get_event_for(state, user, event_id)
    if event.status == STATUS_DELETED:
        raise BettingError("Event not found.")
    return _summarize(state, event, user)


def place_bet(
    user_id: str, event_id: str, side: str, stake: Decimal, now: datetime, path=None
) -> Bet:
    """Place a bet and save the new balance and the bet together; return the bet.

    If the bet takes the member below 10% of the last top-up, the admin is notified.
    """
    _require_aware(now, "now")
    with storage.transaction(path) as data:
        state = _unpack(data, _event_group(data, event_id))
        _close_expired(state, now)
        user = _get_user(state, user_id)
        event = _get_event_for(state, user, event_id)
        auth.require_permission(user, auth.PLACE_BET, event)
        bet = betting.place_bet(user, event, side, stake, now)
        state.bets.append(bet)
        _pack(state, data)
        balances.notify_if_low(data, event.group_id, user_id, bet.stake, now)
    return bet


def resolve_event(
    user_id: str, event_id: str, outcome: str, now: datetime, path=None
) -> Decimal:
    """Set the outcome, pay out and save everything together; return total paid.

    Only the event's creator or the group admin may resolve, and only at or
    after the close time and the event date.
    """
    _require_aware(now, "now")
    with storage.transaction(path) as data:
        state = _unpack(data, _event_group(data, event_id))
        _close_expired(state, now)
        user = _get_user(state, user_id)
        event = _get_event_for(state, user, event_id)
        auth.require_permission(user, auth.RESOLVE_EVENT, event)
        total = betting.resolve_event(
            event, _bets_on(state, event.id), outcome, state.users, now
        )
        _pack(state, data)
        _announce_wins(data, event, _bets_on(state, event.id), now)
    return total


def resolve_over_under(
    user_id: str, event_id: str, actual, now: datetime, path=None
) -> Decimal:
    """Resolve an over/under event by the actual number, pay out and save; return total paid.

    Same permissions and timing as resolve_event. Above the line "over" wins,
    below it "under" wins, exactly on the line every stake is refunded.
    """
    _require_aware(now, "now")
    with storage.transaction(path) as data:
        state = _unpack(data, _event_group(data, event_id))
        _close_expired(state, now)
        user = _get_user(state, user_id)
        event = _get_event_for(state, user, event_id)
        auth.require_permission(user, auth.RESOLVE_EVENT, event)
        total = betting.resolve_over_under(
            event, _bets_on(state, event.id), actual, state.users, now
        )
        _pack(state, data)
        _announce_wins(data, event, _bets_on(state, event.id), now)
    return total


def resolve_names_event(
    user_id: str, event_id: str, winners: List[str], now: datetime, path=None
) -> Decimal:
    """Set the winning option(s) of a names event, pay out and save; return total paid.

    Same permissions and timing as resolve_event. winners are option ids:
    exactly one for a one-winner event, one or more when several can win.
    """
    _require_aware(now, "now")
    with storage.transaction(path) as data:
        state = _unpack(data, _event_group(data, event_id))
        _close_expired(state, now)
        user = _get_user(state, user_id)
        event = _get_event_for(state, user, event_id)
        auth.require_permission(user, auth.RESOLVE_EVENT, event)
        total = betting.resolve_names_event(
            event, _bets_on(state, event.id), winners, state.users, now
        )
        _pack(state, data)
        _announce_wins(data, event, _bets_on(state, event.id), now)
    return total


def group_member_names(user_id: str, path=None, group_id: Optional[str] = None) -> List[str]:
    """Return the usernames in the user's group (or in group_id, if they are in it), sorted."""
    state = _load(path, group_id)
    user = _get_user(state, user_id)
    if not user.group_id:
        return []
    names = [u.username for u in state.users.values() if u.group_id == user.group_id]
    return sorted(names, key=str.casefold)


def bet_history(user_id: str, path=None) -> List[BetRecord]:
    """Return the user's bets with each event's title and result, newest event first."""
    state = _load(path)
    return _bet_history(state, _get_user(state, user_id))


def leaderboard(user_id: str, path=None) -> List[LeaderboardRow]:
    """Return the members of the user's group ranked by balance (highest first).

    Members with the same balance share a rank and are listed by username.
    """
    state = _load(path)
    return _leaderboard(state, _get_user(state, user_id))


def set_avatar(user_id: str, avatar: str, path=None) -> User:
    """Save the user's own profile picture (one of AVATARS) and return the user.

    Only the logged-in user's id is passed in, so users can change only their own.
    """
    if avatar not in AVATARS:
        raise BettingError("Pick one of the animal pictures.")
    with storage.transaction(path) as data:
        state = _unpack(data)
        user = _get_user(state, user_id)
        user.avatar = avatar
        _pack(state, data)
    return user


# --- win announcements (for the confetti) ----------------------------------------
#
# When a result is set, every member who won coins on the event gets an unread
# notification with the id "win:<event id>:<user id>". The Feed shows confetti
# for unread ones and then marks them read, so each win is celebrated once, on
# whatever device the winner uses next. Data saved before this has no such
# notifications, so old wins never set off confetti.

WIN_PREFIX = "win:"


def _net_won(bets: List[Bet], user_id: str) -> Decimal:
    """Return what the user's bets on one resolved event paid on top of their stakes."""
    mine = [bet for bet in bets if bet.user_id == user_id]
    return sum((bet.payout or Decimal("0")) - bet.stake for bet in mine) if mine else Decimal("0")


def _announce_wins(data: dict, event: Event, bets: List[Bet], now: datetime) -> None:
    """Add an unread "You called it" notification for each member who won coins on the event.

    Refunds and members whose bets lost (or lost more than they won, when they
    backed several sides) get none.
    """
    for user_id in dict.fromkeys(bet.user_id for bet in bets):
        if _net_won(bets, user_id) > 0:
            note = Notification(
                id=f"{WIN_PREFIX}{event.id}:{user_id}", user_id=user_id, group_id=event.group_id,
                text=f"You called it: “{event.title}”", created_at=now,
            )
            data["notifications"].append(notification_to_dict(note))


def _unseen_win_notes(data: dict, user_id: str) -> List[dict]:
    """Return the user's unread win notifications, oldest first."""
    return [note for note in data["notifications"]
            if note["user_id"] == user_id and not note["read"] and note["id"].startswith(WIN_PREFIX)]


def _new_wins(data: dict, state: _State, user_id: str) -> List[NewWin]:
    """Return the user's announced wins they have not seen yet, with the coins won."""
    wins = []
    for note in _unseen_win_notes(data, user_id):
        event_id = note["id"][len(WIN_PREFIX):].rsplit(":", 1)[0]
        event = state.events.get(event_id)
        if event is not None:
            wins.append(NewWin(notification_id=note["id"], event_title=event.title,
                               amount=_net_won(_bets_on(state, event_id), user_id)))
    return wins


def has_new_win(user_id: str, path=None) -> bool:
    """Return True if a result has made the user a winner they have not seen yet (one read)."""
    return bool(_unseen_win_notes(storage.load_data(path), user_id))


def mark_wins_seen(user_id: str, notification_ids: List[str], path=None) -> None:
    """Mark the user's win notifications as read, so their confetti never plays again.

    Only the user's own notifications change; unknown ids are ignored.
    """
    wanted = set(notification_ids)
    with storage.transaction(path) as data:
        for note in data["notifications"]:
            if note["user_id"] == user_id and note["id"] in wanted:
                note["read"] = True

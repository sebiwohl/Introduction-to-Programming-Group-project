"""The Feed page: event items with odds and betting, the side cards and the New event dialog."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

import streamlit as st

from src import auth, service, stats
from src.betting import BettingError
from src.models import SIDE_NO, SIDE_YES, STATUS_CLOSED, STATUS_OPEN, STATUS_PAUSED, STATUS_RESOLVED, User
from src.service import BetRecord, EventSummary, PageData
from src.stats import MemberStats
from src.ui import confetti, nav, style
from src.ui.actions import attempt

SIDES = (SIDE_YES, SIDE_NO)
FILTER_KEY = "tabs_feed"
CONFETTI_SHOWN_KEY = "confetti_shown"  # bet ids that already had their confetti this session
CONFETTI_PARAM = "confetti"  # add ?confetti to the address to see it on demand (for the demo)
DEMO_WIN = Decimal("22")  # the amount ?confetti shows when the user has not won anything yet


def bets_by_event(records: List[BetRecord]) -> Dict[str, List[BetRecord]]:
    """Group the user's own bets by event id."""
    grouped: Dict[str, List[BetRecord]] = {}
    for record in records:
        grouped.setdefault(record.bet.event_id, []).append(record)
    return grouped


def _may_resolve(user: User, summary: EventSummary) -> bool:
    """Return True if the shared permission check lets this user set the result."""
    try:
        auth.require_permission(user, auth.RESOLVE_EVENT, summary.event)
    except BettingError:
        return False
    return True


def _members_in(summary: EventSummary) -> int:
    """Return how many different members have bet on the event."""
    return len({b.username for b in summary.bettors})


def _my_calls(user: User, summary: EventSummary) -> str:
    """Describe the user's stakes on one event, like 'you backed NO with 30'."""
    calls = [b for b in summary.bettors if b.username == user.username]
    if not calls:
        return ""
    parts = [f"{b.side.upper()} with {style.money(b.stake)}" for b in calls]
    return "you backed " + " and ".join(parts)


def _my_result(records: List[BetRecord]) -> str:
    """Describe what the user got back from a resolved event."""
    if not records:
        return "you did not bet"
    staked = sum((r.bet.stake for r in records), Decimal("0"))
    paid = sum((r.bet.payout or Decimal("0") for r in records), Decimal("0"))
    if paid == staked:
        return "your stake was returned"
    return f"you won {style.signed(paid - staked)}" if paid > staked else f"you lost {style.money(staked - paid)}"


def _tags(summary: EventSummary, biggest: bool) -> str:
    """Return the status pills and the time line at the top of an event."""
    event = summary.event
    if event.status == STATUS_OPEN:
        pills = style.pill("Open", "open") + (style.pill("Biggest pool", "big") if biggest else "")
        meta = f"Closes {style.when(event.close_time)}"
    elif event.status == STATUS_CLOSED:
        pills, meta = style.pill("Awaiting result", "waiting"), f"Betting closed {style.when(event.close_time)}"
    elif event.status == STATUS_RESOLVED:
        side = event.outcome or SIDE_YES
        pills = style.pill("Resolved", "resolved") + style.pill(f"{side} won", side)
        meta = f"Closed {style.when(event.close_time)}"
    else:
        pills, meta = style.pill(event.status, "paused"), "Betting paused"
    return f'<div class="ci-ev-tags">{pills}<span>{style.esc(meta)}</span></div>'


def _sub_line(user: User, summary: EventSummary, records: List[BetRecord]) -> str:
    """Return the grey line under the title: creator, who is in, pool, your part."""
    event = summary.event
    creator = "you" if event.creator_id == user.id else summary.creator_name
    pool = f"pool {style.money(summary.pool)}"
    if event.status == STATUS_CLOSED:
        waiting = ("Waiting for you to set the result" if event.creator_id == user.id
                   else f"Waiting for {summary.creator_name} (creator) or the admin to set the result")
        parts = [waiting, _my_calls(user, summary), pool]
    elif event.status == STATUS_RESOLVED:
        parts = [f"Created by {creator}", pool, _my_result(records)]
    else:
        count = _members_in(summary)
        members = f"{count} member{'s' if count != 1 else ''} in" if count else ""
        parts = [f"Created by {creator}", members, pool, _my_calls(user, summary)]
    return " · ".join(p for p in parts if p)


def _event_head(user: User, summary: EventSummary, records: List[BetRecord], biggest: bool) -> str:
    """Return the top of an event item: pills, title, grey line, odds boxes and the bar."""
    event = summary.event
    settled = event.status != STATUS_OPEN
    winner = event.outcome if event.status == STATUS_RESOLVED else None
    description = (f'<div class="ci-ev-desc">{style.esc(event.description)}</div>'
                   if event.description else "")
    middle = {STATUS_CLOSED: "Final odds · betting locked",
              STATUS_PAUSED: "Betting paused"}.get(event.status, "")
    return (
        f'<div class="ci-ev"><div class="ci-ev-main">{_tags(summary, biggest)}'
        f'<div class="ci-ev-title">{style.esc(event.title)}</div>{description}'
        f'<div class="ci-ev-sub">{style.esc(_sub_line(user, summary, records))}</div></div>'
        f"{style.odds_boxes(summary.odds_yes, summary.odds_no, settled, winner)}</div>"
        + style.stake_bar(summary.stake_yes, summary.stake_no, middle,
                          grey=event.status == STATUS_PAUSED)
    )


def _side_panel(user: User, summary: EventSummary, side: str) -> str:
    """Return the list of members who backed one side, largest stake first."""
    bettors = [b for b in summary.bettors if b.side == side]
    rows = "".join(
        f'<div class="ci-side-row">{style.who(b.username, b.username == user.username)}'
        f"<b>{style.money(b.stake)}</b></div>"
        for b in bettors
    ) or '<div class="ci-muted">Nobody yet.</div>'
    label = f"{side.upper()} · {len(bettors)} member{'s' if len(bettors) != 1 else ''}"
    return f'<div class="ci-side"><div class="ci-label ci-{side}">{label}</div>{rows}</div>'


def _bets_panel(user: User, summary: EventSummary) -> None:
    """Draw the fold-out list of who bet on each side (it opens without reloading)."""
    count = _members_in(summary)
    if not count:
        return
    with st.container(key=f"bets_{summary.event.id}"):
        with st.expander(f"See who bet ({count})"):
            style.show('<div class="ci-sides">'
                       + "".join(_side_panel(user, summary, side) for side in SIDES) + "</div>")


def _bet_form(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the stake field and the two buttons that place a bet.

    They sit in a form, so changing the stake does not rerun the page (and reload
    the data); only pressing YES or NO does. Enter does not submit, so it cannot
    pick a side by accident.
    """
    event = summary.event
    with st.form(f"bet_{event.id}", border=False, enter_to_submit=False):
        stake_col, yes_col, no_col = st.columns([1.2, 1, 1], vertical_alignment="center")
        stake = stake_col.number_input(
            "Stake", min_value=0.0, value=10.0, step=10.0, format="%.2f",
            key=f"stake_{event.id}", label_visibility="collapsed",
        )
        pressed = [
            side for column, side in ((yes_col, SIDE_YES), (no_col, SIDE_NO))
            if column.form_submit_button(f"Bet {side.upper()}", key=f"{side}_{event.id}",
                                         width="stretch")
        ]
    for side in pressed:
        amount = Decimal(f"{stake:.2f}")
        attempt(
            lambda: service.place_bet(user.id, event.id, side, amount, now),
            f"Bet placed: {style.money(amount)} on {side.upper()}.",
        )
        st.rerun()


def _resolve_row(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the two buttons that set the result of a closed event."""
    event = summary.event
    label_col, yes_col, no_col = st.columns([1.2, 1, 1], vertical_alignment="center")
    style.show(where=label_col, markup='<div class="ci-label">Set the result</div>')
    choices = ((yes_col, SIDE_YES, "YES happened"), (no_col, SIDE_NO, "NO, it didn't"))
    for column, side, label in choices:
        if column.button(label, key=f"{side}_result_{event.id}", width="stretch"):
            attempt(
                lambda: service.resolve_event(user.id, event.id, side, now),
                f"Result set to {side.upper()}. Winners are paid out.",
            )
            st.rerun()


def _event_item(user: User, summary: EventSummary, records: List[BetRecord],
                now: datetime, biggest: bool) -> None:
    """Draw one event; one waiting for its result gets the dashed orange frame."""
    event = summary.event
    prefix = "wait" if event.status == STATUS_CLOSED else "ev"
    with st.container(key=f"{prefix}_{event.id}"):
        style.show(_event_head(user, summary, records, biggest))
        if event.status == STATUS_OPEN:
            _bet_form(user, summary, now)
        elif event.status == STATUS_CLOSED and _may_resolve(user, summary):
            _resolve_row(user, summary, now)
        _bets_panel(user, summary)


def _biggest_pool_id(summaries: List[EventSummary]) -> Optional[str]:
    """Return the id of the open event with the largest pool (only if 2+ are open)."""
    open_ones = [s for s in summaries if s.event.status == STATUS_OPEN and s.pool > 0]
    if len(open_ones) < 2:
        return None
    return max(open_ones, key=lambda s: s.pool).event.id


def _win_banner(page: PageData, me: MemberStats) -> str:
    """Return the black 'You called it' banner if the user's latest settled bet won."""
    record = stats.latest_win(page.my_bets)
    if record is None:
        return ""
    summary = next((s for s in page.events if s.event.id == record.bet.event_id), None)
    odds = stats.side_odds(summary, record.outcome) if summary else None
    side = (record.outcome or "").upper()
    sub = f"{side} won at {style.odds_text(odds)}"
    if me.streak >= 2:
        sub += f" · your streak is now {me.streak}"
    profit = record.bet.payout - record.bet.stake
    return (
        f'<div class="ci-banner"><div><div class="ci-kicker">You called it</div>'
        f'<div class="ci-banner-title">{style.esc(record.event_title)}</div>'
        f'<div class="ci-banner-sub">{style.esc(sub)}</div></div>'
        f'<div class="ci-banner-win">{style.signed(profit)} <small>COINS</small></div></div>'
    )


def _feed_kicker(summaries: List[EventSummary]) -> str:
    """Return the line under the group name, like '2 open · 1 awaiting result'."""
    counts = stats.count_by_status(summaries)
    names = ((STATUS_OPEN, "open"), (STATUS_PAUSED, "paused"),
             (STATUS_CLOSED, "awaiting result"), (STATUS_RESOLVED, "done"))
    parts = [f"{counts[status]} {label}" for status, label in names if counts.get(status)]
    return " · ".join(parts) or "No events yet"


def standing_card(page: PageData, me: MemberStats) -> str:
    """Return the 'Your standing' card: rank, gap to the next member, hit rate, streak."""
    rows = page.leaderboard
    mine = next((r for r in rows if r.username == page.user.username), None)
    if mine is None:
        return ""
    above = stats.next_above(rows, page.user.username)
    gap = (f"{style.money(above.balance - mine.balance)} coins behind {style.esc(above.username)}"
           if above else "Top of the group")
    lines = [f'<div style="font-weight:700;margin:8px 0 10px">{gap}</div>']
    if me.hit_rate is not None:
        percent = round(me.hit_rate * 100)
        lines.append(
            f'<div class="ci-row ci-muted" style="font-size:.78rem"><b style="color:#111">Hit rate'
            f" {percent}%</b><span>{me.right} of {me.settled} calls right</span></div>"
            f'<div class="ci-progress"><div style="width:{percent}%"></div></div>'
        )
    if me.streak >= 1:
        calls = "call" if me.streak == 1 else "calls"
        lines.append(f'<div class="ci-streak" style="font-size:.8rem;font-weight:700;margin-top:8px">'
                     f"{style.FLAME} {me.streak} correct {calls} in a row</div>")
    return (
        f'<div class="ci-card"><div class="ci-kicker">Your standing</div>'
        f'<div style="margin-top:8px"><span class="ci-big">#{mine.rank}</span> '
        f'<span class="ci-muted">of {len(rows)} · {style.money(mine.balance)} coins</span></div>'
        + "".join(lines) + "</div>"
    )


def board_rows(page: PageData) -> str:
    """Return the compact leaderboard lines; the viewer's own line is shaded."""
    lines = []
    for row in page.leaderboard:
        is_me = row.username == page.user.username
        extra = " ci-board-me" if is_me else ""
        lines.append(
            f'<div class="ci-board-row{extra}"><span class="ci-rank">{row.rank}</span>'
            f"{style.who(row.username, is_me, row.is_admin)}<b>{style.money(row.balance)}</b></div>"
        )
    return "".join(lines)


def _nudge_card(page: PageData) -> str:
    """Return the black 'You vs the group' card for an open event the user skipped."""
    found = stats.nudge(page.events, page.user.username)
    if found is None:
        return ""
    side = "YES" if found.side == SIDE_YES else "NO"
    members = f"{found.bettors} member{'s' if found.bettors != 1 else ''} called it"
    return (
        f'<div class="ci-card ci-card-dark"><div class="ci-kicker">You vs the group</div>'
        f'<div style="font-weight:800;font-size:1.05rem;margin-top:8px;line-height:1.35">'
        f"{found.share}% of the pool says {side} on “{style.esc(found.summary.event.title)}”. "
        f"{members}, you haven't yet.</div></div>"
    )


def _side_column(page: PageData, me: MemberStats) -> None:
    """Draw the right column: standing, mini leaderboard and the nudge."""
    style.show(standing_card(page, me))
    with st.container(key="card_board"):
        style.show(f'<div class="ci-kicker" style="margin-bottom:6px">Leaderboard</div>{board_rows(page)}')
        st.button("Full leaderboard →", key="to_board", type="tertiary",
                  on_click=nav.go_to, args=(nav.LEADERBOARD,))
    style.show(_nudge_card(page))


def _amount_won(page: PageData) -> str:
    """Return what the user's latest win made on top of the stake, like '+15' ('' if none)."""
    record = stats.latest_win(page.my_bets)
    return style.signed(record.bet.payout - record.bet.stake) if record else ""


def _confetti(page: PageData) -> str:
    """Return confetti and the amount won for the user's latest win, once per session, else ''.

    ?confetti in the address plays it once on demand (with a sample amount if
    the user has no win yet), for the demo.
    """
    if CONFETTI_PARAM in st.query_params:
        del st.query_params[CONFETTI_PARAM]  # so it plays one time, not on every click
        return confetti.markup(_amount_won(page) or style.signed(DEMO_WIN))
    shown = st.session_state.setdefault(CONFETTI_SHOWN_KEY, set())
    bet_id = confetti.win_to_celebrate(page.my_bets, shown)
    if bet_id is None:
        return ""
    shown.add(bet_id)
    return confetti.markup(_amount_won(page))


def events_page(page: PageData, me: MemberStats, now: datetime) -> None:
    """Draw the Feed: group name, win banner, filter tabs and events; cards on the right."""
    style.show(style.heading(page.group.name, _feed_kicker(page.events)))
    left, right = st.columns([2.5, 1], gap="large")
    with left:
        style.show(_confetti(page))
        style.show(_win_banner(page, me))
        choice = st.radio("Show", stats.FEED_FILTERS, horizontal=True, key=FILTER_KEY,
                          label_visibility="collapsed")
        shown = stats.feed_order(stats.filter_events(page.events, choice, now))
        if not shown:
            st.info("Nothing here yet. Press New event at the top to start one.")
        mine = bets_by_event(page.my_bets)
        biggest = _biggest_pool_id(page.events)
        for summary in shown:
            _event_item(page.user, summary, mine.get(summary.event.id, []), now,
                        summary.event.id == biggest)
    with right:
        _side_column(page, me)


@st.dialog("New event")
def new_event_dialog(user: User, now: datetime) -> None:
    """Show the form that creates a new yes/no event in a pop-up."""
    today = now.astimezone(style.APP_TIMEZONE).date()
    with st.form("create_event", border=False):
        title = st.text_input("Question", max_chars=service.MAX_TITLE_LENGTH,
                              placeholder="Will Ben finish the marathon?")
        description = st.text_area("Details (optional)",
                                   max_chars=service.MAX_DESCRIPTION_LENGTH, height=80)
        date_col, time_col = st.columns(2)
        day: date = date_col.date_input("Betting closes on", min_value=today,
                                        value=today + timedelta(days=1))
        clock: time = time_col.time_input("At (Lisbon time)", value=time(18, 0))
        if st.form_submit_button("Create event", type="primary", width="stretch"):
            closes = datetime.combine(day, clock, tzinfo=style.APP_TIMEZONE)
            attempt(
                lambda: service.create_event(user.id, title, closes, now, description),
                "Event created.",
            )
            st.rerun()

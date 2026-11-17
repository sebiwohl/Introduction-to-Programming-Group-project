"""The main page: open events, past events, the create form and the leaderboard."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List

import streamlit as st

from src import auth, service
from src.betting import BettingError
from src.models import SIDE_NO, SIDE_YES, STATUS_CLOSED, STATUS_OPEN, STATUS_RESOLVED, User
from src.service import BetRecord, EventSummary, LeaderboardRow, PageData
from src.ui import style
from src.ui.actions import attempt

SIDE_LABEL = {SIDE_YES: "Yes", SIDE_NO: "No"}
STATUS_BADGE = {
    STATUS_OPEN: ("Open", "open"),
    STATUS_CLOSED: ("Closed · waiting for result", "waiting"),
}


def _bets_by_event(records: List[BetRecord]) -> Dict[str, List[BetRecord]]:
    """Group the user's own bets by event id."""
    grouped: Dict[str, List[BetRecord]] = {}
    for record in records:
        grouped.setdefault(record.bet.event_id, []).append(record)
    return grouped


def _my_bets_line(records: List[BetRecord]) -> str:
    """Describe the user's bets on one event, with the payout once it is known."""
    if not records:
        return ""
    totals = {side: Decimal("0") for side in SIDE_LABEL}
    for record in records:
        totals[record.bet.side] += record.bet.stake
    staked = " and ".join(
        f"{style.money(total)} on {SIDE_LABEL[side]}"
        for side, total in totals.items() if total
    )
    line = f"You staked {staked}"
    if all(r.bet.payout is not None for r in records):
        paid = sum((r.bet.payout for r in records), Decimal("0"))
        line += f" · paid out {style.money(paid)}"
    return line


def _may_resolve(user: User, summary: EventSummary) -> bool:
    """Return True if the shared permission check lets this user set the result."""
    try:
        auth.require_permission(user, auth.RESOLVE_EVENT, summary.event)
    except BettingError:
        return False
    return True


def _odds_label(side: str, odds) -> str:
    """Return the button text, like 'Yes × 2.86' (no number before any bet)."""
    return f"{SIDE_LABEL[side]} × {odds:.2f}" if odds else SIDE_LABEL[side]


def _bet_row(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the stake field and the two buttons that place a bet.

    They sit in a form, so changing the stake does not rerun the page (and reload
    the data); only pressing Yes or No does. Enter does not submit, so it cannot
    pick a side by accident.
    """
    event = summary.event
    with st.form(f"bet_{event.id}", border=False, enter_to_submit=False):
        stake_col, yes_col, no_col = st.columns([1, 1.3, 1.3])
        stake = stake_col.number_input(
            "Stake", min_value=0.0, value=10.0, step=10.0, format="%.2f",
            key=f"stake_{event.id}", label_visibility="collapsed",
        )
        choices = ((yes_col, SIDE_YES, summary.odds_yes), (no_col, SIDE_NO, summary.odds_no))
        pressed = [
            side for column, side, odds in choices
            if column.form_submit_button(
                _odds_label(side, odds), key=f"{side}_{event.id}", width="stretch"
            )
        ]
    for side in pressed:
        amount = Decimal(f"{stake:.2f}")
        attempt(
            lambda: service.place_bet(user.id, event.id, side, amount, now),
            f"Bet placed: {style.money(amount)} on {SIDE_LABEL[side]}.",
        )
        st.rerun()


def _resolve_row(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the two buttons that set the result of a closed event."""
    event = summary.event
    label_col, yes_col, no_col = st.columns([1, 1.3, 1.3])
    label_col.markdown(style.muted("Set the result:"), unsafe_allow_html=True)
    choices = ((yes_col, SIDE_YES, "Yes happened"), (no_col, SIDE_NO, "No, it didn't"))
    for column, side, label in choices:
        if column.button(label, key=f"{side}_result_{event.id}", width="stretch"):
            attempt(
                lambda: service.resolve_event(user.id, event.id, side, now),
                f"Result set to {SIDE_LABEL[side]}. Winners are paid out.",
            )
            st.rerun()


def _event_card(user: User, summary: EventSummary, mine: List[BetRecord], now: datetime) -> None:
    """Draw one event that is open or waiting for its result."""
    event = summary.event
    text, kind = STATUS_BADGE.get(event.status, (event.status.capitalize(), "waiting"))
    verb = "Closes" if event.status == STATUS_OPEN else "Closed"
    meta = f"{verb} {style.when(event.close_time)} · pool {style.money(summary.pool)}"
    with st.container(border=True, key=f"card_{event.id}"):
        st.markdown(
            style.card_header(event.title, style.badge(text, kind), event.description, meta)
            + style.odds_bar(summary.stake_yes, summary.stake_no),
            unsafe_allow_html=True,
        )
        if event.status == STATUS_OPEN:
            _bet_row(user, summary, now)
        elif event.status == STATUS_CLOSED and _may_resolve(user, summary):
            _resolve_row(user, summary, now)
        if mine:
            st.markdown(style.muted(_my_bets_line(mine)), unsafe_allow_html=True)


def _past_card(summary: EventSummary, mine: List[BetRecord]) -> None:
    """Draw one resolved event: read-only, with the result and the user's payout."""
    event = summary.event
    text = f"Resolved · {SIDE_LABEL.get(event.outcome, '?')} won"
    meta = f"Closed {style.when(event.close_time)} · pool {style.money(summary.pool)}"
    with st.container(border=True, key=f"card_{event.id}"):
        st.markdown(
            style.card_header(event.title, style.badge(text, "resolved"), event.description, meta)
            + style.odds_bar(summary.stake_yes, summary.stake_no),
            unsafe_allow_html=True,
        )
        line = _my_bets_line(mine) or "You did not bet on this event."
        st.markdown(style.muted(line), unsafe_allow_html=True)


def _create_event_form(user: User, now: datetime) -> None:
    """Draw the form that creates a new yes/no event."""
    today = now.astimezone(style.APP_TIMEZONE).date()
    with st.expander("Create new event"):
        with st.form("create_event", clear_on_submit=True):
            title = st.text_input("Title", max_chars=service.MAX_TITLE_LENGTH,
                                  placeholder="Ben finishes the marathon")
            description = st.text_area("Description (optional)",
                                       max_chars=service.MAX_DESCRIPTION_LENGTH, height=80)
            date_col, time_col = st.columns(2)
            day: date = date_col.date_input("Betting closes on", min_value=today,
                                            value=today + timedelta(days=1))
            clock: time = time_col.time_input("At (Lisbon time)", value=time(18, 0))
            if st.form_submit_button("Create event", type="primary"):
                closes = datetime.combine(day, clock, tzinfo=style.APP_TIMEZONE)
                attempt(
                    lambda: service.create_event(user.id, title, closes, now, description),
                    "Event created.",
                )
                st.rerun()


def leaderboard_panel(user: User, rows: List[LeaderboardRow]) -> None:
    """Draw the group's leaderboard, with the viewer's own line outlined."""
    st.markdown('<div class="ci-heading">Leaderboard</div>', unsafe_allow_html=True)
    st.markdown(
        "".join(
            style.board_row(r.rank, r.username, r.balance, r.username == user.username)
            for r in rows
        ),
        unsafe_allow_html=True,
    )


def events_page(page: PageData, now: datetime) -> None:
    """Draw the main page: tabs with events in the middle, leaderboard on the right."""
    user, summaries = page.user, page.events
    mine = _bets_by_event(page.my_bets)
    current = [s for s in summaries if s.event.status != STATUS_RESOLVED]
    past = [s for s in summaries if s.event.status == STATUS_RESOLVED]
    middle, right = st.columns([5, 2], gap="medium")
    with middle:
        open_tab, past_tab = st.tabs([f"Open events ({len(current)})",
                                      f"Past events ({len(past)})"])
        with open_tab:
            if not current:
                st.info("No open events. Create the first one below.")
            for summary in current:
                _event_card(user, summary, mine.get(summary.event.id, []), now)
            _create_event_form(user, now)
        with past_tab:
            if not past:
                st.info("No past events yet. Events appear here once their result is set.")
            for summary in past:
                _past_card(summary, mine.get(summary.event.id, []))
    with right:
        leaderboard_panel(user, page.leaderboard)

"""The Feed page: event items with odds and betting, the side cards and the New event dialog.

Yes/no events show two odds boxes and a two-colour bar. Names events (the
options are names, such as people in the group) show one row per option
instead, built from the same pieces and colours.
"""

import time as clock
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import streamlit as st

from src import auth, betting, moderation, service, stats
from src.betting import BettingError
from src.models import SIDE_NO, SIDE_YES, STATUS_CLOSED, STATUS_OPEN, STATUS_PAUSED, STATUS_RESOLVED, User
from src.service import BetRecord, EventSummary, PageData
from src.stats import MemberStats
from src.ui import confetti, nav, style
from src.storage import StorageError
from src.ui.actions import FLASH_KEY, attempt

SIDES = (SIDE_YES, SIDE_NO)


def _labels(event) -> tuple:
    """Return the short names of the two sides: OVER / UNDER or YES / NO."""
    return ("OVER", "UNDER") if betting.is_over_under(event) else ("YES", "NO")


def _short(event, side: str) -> str:
    """Return the short name of one side (OVER, UNDER, YES or NO)."""
    return _labels(event)[0 if side == SIDE_YES else 1]
FILTER_KEY = "tabs_feed"
KIND_CHOICES = ("Yes / No", "Names", "Over / Under")
WINNER_CHOICES = ("One winner", "Several can win")
SEVERAL_HELP = ("Several names can win. Everyone who backed a winning name shares the whole pool, "
                "so a name's odds are the most it can pay: the more names win, the less each pays.")
CLOSES_HELP = ("Betting always closes no later than the event date: betting while the result "
               "is known would give free profits. The result can be set after the event date.")
WIN_CHECK_SECONDS = 30  # while the Feed is open, look for a newly announced win this often
LAST_LOAD_KEY = "feed_loaded_at"  # when the Feed last loaded fully (it then checked for wins itself)
CONFETTI_PARAM = "confetti"  # add ?confetti to the address to see it on demand (for the demo)
DEMO_WIN = Decimal("22")  # the amount ?confetti shows when the user has not won anything yet


def _now() -> datetime:
    """Return the current time.

    The names bet form and the result picker are fragments: clicking in them
    reruns only that part of the page, so the `now` of the last full page load
    may be minutes old. Actions there read the clock when they happen, so a
    bet placed after betting closed is still refused. The New event dialog is
    a fragment too, so it checks the close time against this as well.
    """
    return datetime.now(timezone.utc)


def bets_by_event(records: List[BetRecord]) -> Dict[str, List[BetRecord]]:
    """Group the user's own bets by event id."""
    grouped: Dict[str, List[BetRecord]] = {}
    for record in records:
        grouped.setdefault(record.bet.event_id, []).append(record)
    return grouped


def _may(user: User, action: str, summary: EventSummary) -> bool:
    """Return True if the shared permission check lets this user do `action` on the event.

    Only decides which buttons to show; every action is checked again where it happens.
    """
    try:
        auth.require_permission(user, action, summary.event)
    except BettingError:
        return False
    return True


def _may_resolve(user: User, summary: EventSummary) -> bool:
    """Return True if the shared permission check lets this user set the result."""
    return _may(user, auth.RESOLVE_EVENT, summary)


def _members_in(summary: EventSummary) -> int:
    """Return how many different members have bet on the event."""
    return len({b.username for b in summary.bettors})


def _my_calls(user: User, summary: EventSummary) -> str:
    """Describe the user's stakes on one event, like 'you backed NO with 30'."""
    calls = [b for b in summary.bettors if b.username == user.username]
    if not calls:
        return ""
    parts = [f"{betting.side_label(summary.event, b.side)} with {style.money(b.stake)}" for b in calls]
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


def _result_due(event, now: datetime) -> bool:
    """Return True once the event date has passed, so its result can be set."""
    return now >= event.event_time


def _tags(summary: EventSummary, biggest: bool, now: datetime) -> str:
    """Return the status pills and the time line at the top of an event.

    When the event happens after betting closes, the line shows both times.
    """
    event = summary.event
    if event.status == STATUS_OPEN:
        pills = style.pill("Open", "open") + (style.pill("Biggest pool", "big") if biggest else "")
        meta = f"Closes {style.when(event.close_time)}"
        if event.event_time != event.close_time:
            meta += f" · Event {style.when(event.event_time)}"
    elif event.status == STATUS_CLOSED and not _result_due(event, now):
        pills = style.pill("Awaiting result", "waiting")
        meta = f"Betting closed · result after {style.when(event.event_time)}"
    elif event.status == STATUS_CLOSED:
        pills, meta = style.pill("Awaiting result", "waiting"), f"Betting closed {style.when(event.close_time)}"
    elif event.status == STATUS_RESOLVED and betting.is_names_event(event):
        # the same pill as a yes/no result ("yes won")
        pills = style.pill("Resolved", "resolved") + style.pill(f"{service.winner_label(event)} won", SIDE_YES)
        meta = f"Closed {style.when(event.close_time)}"
    elif event.status == STATUS_RESOLVED and betting.is_over_under(event):
        result = (style.pill(f"{service.winner_label(event)} won", event.outcome)
                  if event.outcome else style.pill(service.winner_label(event), "resolved"))
        pills = style.pill("Resolved", "resolved") + result
        meta = f"Closed {style.when(event.close_time)}"
    elif event.status == STATUS_RESOLVED:
        side = event.outcome or SIDE_YES
        pills = style.pill("Resolved", "resolved") + style.pill(f"{side} won", side)
        meta = f"Closed {style.when(event.close_time)}"
    else:
        pills, meta = style.pill(event.status, "paused"), "Betting paused"
    return f'<div class="ci-ev-tags">{pills}{_mode_pill(event)}<span>{style.esc(meta)}</span></div>'


def _mode_pill(event) -> str:
    """Return the 'One winner' / 'Several can win' pill of a names event, the line of an
    over/under event, or nothing for yes/no."""
    if betting.is_over_under(event):
        return style.pill(f"Over / under {event.line}", "refunded")
    if not betting.is_names_event(event):
        return ""
    if not event.multi_winner:
        return style.pill(WINNER_CHOICES[0], "refunded")
    return (f'<span class="ci-pill ci-pill-refunded" title="{style.esc(SEVERAL_HELP)}">'
            f'{WINNER_CHOICES[1]}<span class="ci-help">?</span></span>')


def _sub_line(user: User, summary: EventSummary, records: List[BetRecord], now: datetime) -> str:
    """Return the grey line under the title: creator, who is in, pool, your part."""
    event = summary.event
    creator = "you" if event.creator_id == user.id else summary.creator_name
    pool = f"pool {style.money(summary.pool)}"
    if event.status == STATUS_CLOSED and not _result_due(event, now):  # the time line says when
        parts = [f"Created by {creator}", _my_calls(user, summary), pool]
    elif event.status == STATUS_CLOSED:
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


def _ranked_options(summary: EventSummary) -> list:
    """Return a names event's options with the biggest pool share first (listed order on ties)."""
    return sorted(summary.options, key=lambda option: -(option.share or 0))


def _names_head(user: User, summary: EventSummary, records: List[BetRecord], biggest: bool,
                now: datetime) -> str:
    """Return the top of a names event: pills, title, grey line and one row per option."""
    event = summary.event
    description = (f'<div class="ci-ev-desc">{style.esc(event.description)}</div>'
                   if event.description else "")
    up_to = event.multi_winner and event.status != STATUS_RESOLVED
    rows = "".join(
        style.option_row(option.name, stats.side_odds(summary, option.id), option.share, option.stake,
                         len({b.username for b in summary.bettors if b.side == option.id}),
                         option.my_stake, up_to, option.won)
        for option in _ranked_options(summary)
    )  # side_odds: a winning name shows what was actually paid
    look = {STATUS_OPEN: "", STATUS_PAUSED: " ci-opts-grey"}.get(event.status, " ci-opts-final")
    note = {STATUS_CLOSED: "Final odds · betting locked",
            STATUS_PAUSED: "Betting paused"}.get(event.status, "")
    if not note and summary.pool == 0:
        note = "No bets yet"
    note_line = f'<div class="ci-split"><span class="ci-split-mid">{note}</span></div>' if note else ""
    return (
        f'<div class="ci-ev"><div class="ci-ev-main">{_tags(summary, biggest, now)}'
        f'<div class="ci-ev-title">{style.esc(event.title)}</div>{description}'
        f'<div class="ci-ev-sub">{style.esc(_sub_line(user, summary, records, now))}</div></div></div>'
        f'{note_line}<div class="ci-opts{look}">{rows}</div>'
    )


def _event_head(user: User, summary: EventSummary, records: List[BetRecord], biggest: bool,
                now: datetime) -> str:
    """Return the top of an event item: pills, title, grey line, odds boxes and the bar."""
    event = summary.event
    if betting.is_names_event(event):
        return _names_head(user, summary, records, biggest, now)
    settled = event.status != STATUS_OPEN
    winner = event.outcome if event.status == STATUS_RESOLVED else None
    description = (f'<div class="ci-ev-desc">{style.esc(event.description)}</div>'
                   if event.description else "")
    middle = {STATUS_CLOSED: "Final odds · betting locked",
              STATUS_PAUSED: "Betting paused"}.get(event.status, "")
    return (
        f'<div class="ci-ev"><div class="ci-ev-main">{_tags(summary, biggest, now)}'
        f'<div class="ci-ev-title">{style.esc(event.title)}</div>{description}'
        f'<div class="ci-ev-sub">{style.esc(_sub_line(user, summary, records, now))}</div></div>'
        f"{style.odds_boxes(summary.odds_yes, summary.odds_no, settled, winner, _labels(event))}"
        "</div>"
        + style.stake_bar(summary.stake_yes, summary.stake_no, middle,
                          grey=event.status == STATUS_PAUSED, labels=_labels(event))
    )


def _side_panel(user: User, summary: EventSummary, side: str) -> str:
    """Return the list of members who backed one side, largest stake first."""
    bettors = [b for b in summary.bettors if b.side == side]
    rows = "".join(
        f'<div class="ci-side-row">{style.who(b.username, b.username == user.username)}'
        f"<b>{style.money(b.stake)}</b></div>"
        for b in bettors
    ) or '<div class="ci-muted">Nobody yet.</div>'
    label = (f"{style.esc(betting.side_label(summary.event, side))} · {len(bettors)} "
             f"member{'s' if len(bettors) != 1 else ''}")
    return f'<div class="ci-side"><div class="ci-label ci-{side}">{label}</div>{rows}</div>'


def _option_panel(user: User, summary: EventSummary, option) -> str:
    """Return the list of members who backed one option of a names event."""
    bettors = [b for b in summary.bettors if b.side == option.id]
    rows = "".join(
        f'<div class="ci-side-row">{style.who(b.username, b.username == user.username)}'
        f"<b>{style.money(b.stake)}</b></div>"
        for b in bettors
    ) or '<div class="ci-muted">Nobody yet.</div>'
    label = f"{style.esc(option.name)} · {len(bettors)} member{'s' if len(bettors) != 1 else ''}"
    return f'<div class="ci-side"><div class="ci-label">{label}</div>{rows}</div>'


def _bets_panel(user: User, summary: EventSummary) -> None:
    """Draw the fold-out list of who bet on each side (it opens without reloading)."""
    count = _members_in(summary)
    if not count:
        return
    if betting.is_names_event(summary.event):
        panels = "".join(_option_panel(user, summary, option)
                         for option in _ranked_options(summary) if option.stake)
    else:
        panels = "".join(_side_panel(user, summary, side) for side in SIDES)
    with st.container(key=f"bets_{summary.event.id}"):
        with st.expander(f"See who bet ({count})"):
            style.show(f'<div class="ci-sides">{panels}</div>')


def _preview(summary: EventSummary, pick: Optional[str], amount: Decimal) -> str:
    """Return the line in the bet window, like 'If Marta wins you get ~42 coins'."""
    if pick is None:
        return "Pick a side to see what you could win."
    payout = stats.payout_preview(amount, summary, pick)
    if payout is None:
        return "Enter a stake above 0."
    name = betting.side_label(summary.event, pick)
    up_to = "up to " if summary.event.multi_winner else ""
    return f"If {name} wins you get {up_to}~{style.money(payout)} coins"


@st.dialog("Set the result")
def _confirm_result(user: User, event_id: str, title: str, winners: List[str],
                    names: List[str]) -> None:
    """Ask 'Are you sure?' before the result of a names event is saved, as it cannot be undone."""
    won = service.join_names(names)
    style.show(
        f'<div style="font-weight:800;font-size:1.05rem">{style.esc(title)}</div>'
        f'<p style="margin:10px 0 4px">Winner{"s" if len(names) > 1 else ""}: <b>{style.esc(won)}</b></p>'
        '<p class="ci-muted">Are you sure? Resolving cannot be undone.</p>'
    )
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="confirm_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Yes, set the result", key="confirm_result", type="primary", width="stretch"):
        attempt(
            lambda: service.resolve_names_event(user.id, event_id, winners, _now()),
            f"Result set: {style.plain(won)} won. Winners are paid out.",
        )
        st.rerun()


@st.fragment
def _names_resolve_row(user: User, summary: EventSummary) -> None:
    """Draw the winner picker of a closed names event: one choice, or ticks when several can win."""
    event = summary.event
    labels = {option.id: option.name for option in event.options}
    hint = "tick every name that happened" if event.multi_winner else "pick the name that happened"
    style.show(f'<div class="ci-label">Set the result <span class="ci-muted" '
               f'style="text-transform:none;letter-spacing:0">· {hint}</span></div>')
    if event.multi_winner:
        columns = st.columns(3)
        picked = [option_id for index, option_id in enumerate(labels)
                  if columns[index % 3].checkbox(style.plain(labels[option_id]),
                                                 key=f"win_{event.id}_{option_id}")]
    else:
        one = st.radio("Winner", list(labels), index=None, horizontal=True, key=f"win_{event.id}",
                       format_func=lambda option_id: style.plain(labels[option_id]),
                       label_visibility="collapsed")
        picked = [one] if one else []
    if st.button("Set the result", key=f"names_result_{event.id}", type="primary", disabled=not picked):
        _confirm_result(user, event.id, event.title, picked, [labels[i] for i in picked])


@st.dialog("Set the result")
def _confirm_side_result(user: User, event_id: str, title: str, side: str) -> None:
    """Ask 'Are you sure?' before a yes/no result is saved, as it cannot be undone."""
    style.show(
        f'<div style="font-weight:800;font-size:1.05rem">{style.esc(title)}</div>'
        f'<p style="margin:10px 0 4px">Result: <b>{side.upper()}</b></p>'
        '<p class="ci-muted">Are you sure? Resolving cannot be undone.</p>'
    )
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="side_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Yes, set the result", key="side_confirm", type="primary",
                          width="stretch"):
        attempt(
            lambda: service.resolve_event(user.id, event_id, side, _now()),
            f"Result set to {side.upper()}. Winners are paid out.",
        )
        st.rerun()


@st.dialog("Set the result")
def _confirm_number_result(user: User, event, actual: Decimal) -> None:
    """Ask 'Are you sure?' before an over/under result is saved, as it cannot be undone."""
    if actual == event.line:
        effect = f"Exactly on the line ({event.line}): every stake is refunded."
    else:
        effect = f"{'OVER' if actual > event.line else 'UNDER'} {event.line} wins."
    style.show(
        f'<div style="font-weight:800;font-size:1.05rem">{style.esc(event.title)}</div>'
        f'<p style="margin:10px 0 4px">Actual number: <b>{actual}</b> · {effect}</p>'
        '<p class="ci-muted">Are you sure? Resolving cannot be undone.</p>'
    )
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="number_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Yes, set the result", key="number_confirm", type="primary",
                          width="stretch"):
        attempt(lambda: service.resolve_over_under(user.id, event.id, actual, _now()),
                f"Result set: {actual}. {effect}")
        st.rerun()


def _over_under_resolve_row(user: User, summary: EventSummary) -> None:
    """Draw the field for the actual number of a closed over/under event."""
    event = summary.event
    label_col, number_col, button_col = st.columns([1.2, 1, 1], vertical_alignment="center")
    style.show(where=label_col, markup=f'<div class="ci-label">Actual number '
                                       f'<span class="ci-muted">· line {event.line}</span></div>')
    actual = number_col.number_input("Actual number", value=float(event.line), step=1.0,
                                     format="%.2f", key=f"actual_{event.id}",
                                     label_visibility="collapsed")
    if button_col.button("Set the result", key=f"number_result_{event.id}", width="stretch"):
        # same cleaning as the stored number, so 11.00 shows as 11
        _confirm_number_result(user, event, betting.clean_number(Decimal(f"{actual:.2f}"), "result"))


def _resolve_row(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the two buttons that set the result of a closed event (after 'Are you sure?')."""
    event = summary.event
    label_col, yes_col, no_col = st.columns([1.2, 1, 1], vertical_alignment="center")
    style.show(where=label_col, markup='<div class="ci-label">Set the result</div>')
    choices = ((yes_col, SIDE_YES, "YES happened"), (no_col, SIDE_NO, "NO, it didn't"))
    for column, side, label in choices:
        if column.button(label, key=f"{side}_result_{event.id}", width="stretch"):
            _confirm_side_result(user, event.id, event.title, side)


# --- flags, polls and deleting ----------------------------------------------------

FLAG_MESSAGES = {
    moderation.FLAGGED: "Flagged. If enough members flag it, betting pauses and the group votes.",
    moderation.POLL_OPENED: "Enough flags: betting is paused and the group votes in a poll.",
    moderation.DELETED: "Most members flagged it: the event is deleted and every stake refunded.",
}


def _can_delete(user: User, summary: EventSummary, now: datetime) -> bool:
    """Return True if the user may delete the event now (creator or admin, before 3 h to close)."""
    event = summary.event
    return (event.status in (STATUS_OPEN, STATUS_PAUSED) and _may(user, auth.DELETE_EVENT, summary)
            and now < event.close_time - moderation.DELETE_DEADLINE)


@st.dialog("Delete this event?")
def _confirm_delete(user: User, event_id: str, title: str) -> None:
    """Ask before deleting, as it cannot be undone; every stake is refunded."""
    style.show(f'<div style="font-weight:800;font-size:1.05rem">{style.esc(title)}</div>'
               '<p class="ci-muted" style="margin-top:8px">Every stake is refunded. '
               "This cannot be undone.</p>")
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="delete_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Yes, delete it", key="delete_confirm", type="primary", width="stretch"):
        attempt(lambda: moderation.delete_event(user.id, event_id, _now()),
                "Event deleted. Every stake was refunded.")
        st.rerun()


def _flag(user: User, event_id: str) -> None:
    """Flag the event and say what happened (flagged, poll opened or deleted)."""
    outcome = {}

    def run() -> None:
        outcome["result"] = moderation.flag_event(user.id, event_id, _now())

    if attempt(run):
        st.session_state[FLASH_KEY] = ("success", FLAG_MESSAGES[outcome["result"]])
    st.rerun()


def _menu_key(event_id: str) -> str:
    """Return the session key that says whether an event's ⋮ menu is open."""
    return f"menu_open_{event_id}"


def _toggle_menu(event_id: str) -> None:
    """Open or close an event's ⋮ menu."""
    key = _menu_key(event_id)
    st.session_state[key] = not st.session_state.get(key, False)


def _close_menu(event_id: str) -> None:
    """Close an event's ⋮ menu (after one of its actions)."""
    st.session_state[_menu_key(event_id)] = False


def _actions_menu(user: User, summary: EventSummary, now: datetime) -> None:
    """Draw the ⋮ in the card's top-right corner and, when open, a small menu inside the card.

    It lists Open poll and Delete, and Flag while nobody has flagged yet (after
    that the flag line in the card has the button). Only actions the user may
    take are listed; every action is checked again in moderation.py.
    """
    event = summary.event
    is_open = event.status == STATUS_OPEN
    show_flag = (is_open and not event.flags and _may(user, auth.FLAG_EVENT, summary))
    show_poll = is_open and _may(user, auth.OPEN_POLL, summary)
    show_delete = _can_delete(user, summary, now)
    if not (show_flag or show_poll or show_delete):
        return
    with st.container(key=f"menu_{event.id}"):
        st.button(":material/more_vert:", key=f"menubtn_{event.id}", help="More",
                  on_click=_toggle_menu, args=(event.id,))
    if not st.session_state.get(_menu_key(event.id)):
        return
    # No on_click here: a callback that closes the menu would hide these buttons
    # before Streamlit reads the click, so the action would never run.
    with st.container(key=f"menupanel_{event.id}"):
        if show_flag and st.button("Flag", key=f"flag_{event.id}", icon=":material/flag:",
                                   width="stretch"):
            _close_menu(event.id)
            _flag(user, event.id)
        if show_poll and st.button("Open poll", key=f"poll_{event.id}",
                                   icon=":material/how_to_vote:", width="stretch"):
            _close_menu(event.id)
            attempt(lambda: moderation.open_poll(user.id, event.id, _now()),
                    "Poll opened: betting is paused while the group votes.")
            st.rerun()
        if show_delete and st.button("Delete", key=f"delete_{event.id}", icon=":material/delete:",
                                     width="stretch"):
            _close_menu(event.id)
            _confirm_delete(user, event.id, event.title)


def _flag_line(user: User, summary: EventSummary) -> None:
    """Show in the card that members flagged this open event, with the Flag button."""
    event = summary.event
    count = len(event.flags)
    if event.status != STATUS_OPEN or not count:
        return
    who = f"{count} member{'' if count == 1 else 's'} flagged this"
    with st.container(key=f"flagline_{event.id}", horizontal=True, vertical_alignment="center",
                      gap="small"):
        style.show(f'<div class="ci-flagnote">⚑ {who}. Enough flags pause betting for a vote, '
                   "and each flag counts as a delete vote.</div>")
        if user.id in event.flags:
            style.show('<div class="ci-muted" style="font-size:.78rem">You flagged it</div>')
        elif _may(user, auth.FLAG_EVENT, summary):
            if st.button("Flag", key=f"flagtoo_{event.id}", icon=":material/flag:"):
                _flag(user, event.id)


def _vote_bar(poll) -> str:
    """Return the Keep / Delete bar of a poll: green and red, thin and striped (not the bet bar)."""
    keep = sum(1 for choice in poll.votes.values() if choice == "keep")
    delete = len(poll.votes) - keep
    if not poll.votes:
        return ('<div class="ci-votes"><div class="ci-votes-labels"><span>No votes yet</span></div>'
                '<div class="ci-votebar"></div></div>')
    keep_share = round(keep / len(poll.votes) * 100)
    return (
        '<div class="ci-votes"><div class="ci-votes-labels">'
        f'<span class="ci-vote-keep">KEEP {keep} · {keep_share}%</span>'
        f'<span class="ci-vote-delete">{100 - keep_share}% · {delete} DELETE</span></div>'
        '<div class="ci-votebar">'
        + (f'<div class="ci-votebar-keep" style="flex:{keep}"></div>' if keep else "")
        + (f'<div class="ci-votebar-delete" style="flex:{delete}"></div>' if delete else "")
        + "</div></div>"
    )


def _poll_panel(user: User, summary: EventSummary) -> None:
    """Draw the poll on a paused event: when it ends, the votes so far and the vote buttons."""
    poll, event = summary.poll, summary.event
    if poll is None:
        style.show('<div class="ci-muted">Betting is paused.</div>')
        return
    style.show(
        '<div class="ci-label">Betting is paused · poll: delete this event?</div>'
        f'<div class="ci-muted" style="margin:4px 0 6px">Voting ends {style.when(poll.closes_at)}. '
        "More than half of the votes for Delete removes it and refunds every stake; "
        "a tie or no votes keeps it.</div>"
        + _vote_bar(poll)
    )
    mine = poll.votes.get(user.id)
    if moderation.vote_from_flag(poll.votes, event.flags, user.id):
        style.show('<div class="ci-muted">You voted <b>delete</b> (from your flag). '
                   "You can switch to keep once.</div>")
        if _may(user, auth.VOTE_POLL, summary) and st.button(
                "Switch to keep", key=f"vote_keep_{event.id}", width="stretch"):
            attempt(lambda: moderation.vote(user.id, event.id, "keep", _now()),
                    "Vote saved: keep.")
            st.rerun()
        return
    if mine:
        style.show(f'<div class="ci-muted">You voted <b>{style.esc(mine)}</b>.</div>')
        return
    if not _may(user, auth.VOTE_POLL, summary):
        return
    delete_col, keep_col = st.columns(2)
    for column, choice, label in ((delete_col, "delete", "Delete it"), (keep_col, "keep", "Keep it")):
        if column.button(label, key=f"vote_{choice}_{event.id}", width="stretch"):
            attempt(lambda: moderation.vote(user.id, event.id, choice, _now()),
                    f"Vote saved: {choice}.")
            st.rerun()


COUNTDOWN_SECONDS = 60  # the countdown on open events updates this often (requirement 3.2)


@st.fragment(run_every=COUNTDOWN_SECONDS)
def _countdown(close_time: datetime) -> None:
    """Show the time left until betting closes; red in the last 3 hours.

    A fragment with its own timer: only this line is redrawn every minute, the
    data is not reloaded. Once the time is up it says so; the event itself
    closes at the next page load.
    """
    left = stats.countdown(close_time, _now())
    kind = " ci-countdown-urgent" if left.urgent else ""
    style.show(f'<div class="ci-countdown{kind}">⏱ {style.esc(left.text)}</div>')


def _head_main(user: User, summary: EventSummary, records: List[BetRecord], biggest: bool,
               now: datetime) -> str:
    """Return the left part of an event's top: pills, title, details and the grey line."""
    event = summary.event
    description = (f'<div class="ci-ev-desc">{style.esc(event.description)}</div>'
                   if event.description else "")
    return (f'<div class="ci-ev-main">{_tags(summary, biggest, now)}'
            f'<div class="ci-ev-title">{style.esc(event.title)}</div>{description}'
            f'<div class="ci-ev-sub">{style.esc(_sub_line(user, summary, records, now))}</div></div>')


@st.dialog("Place a bet")
def _bet_dialog(user: User, summary: EventSummary, side: str) -> None:
    """Ask for the stake on the side (or name) that was clicked, then place the bet."""
    event = summary.event
    label = betting.side_label(event, side)
    odds = stats.side_odds(summary, side)
    odds_note = f"odds now {style.odds_text(odds)}" if odds else "no bets on it yet"
    style.show(f'<div style="font-weight:800;font-size:1.05rem">{style.esc(event.title)}</div>'
               f'<p style="margin:8px 0 2px">Your call: <b>{style.esc(label)}</b> · '
               f'<span class="ci-muted">{odds_note}</span></p>')
    stake = st.number_input("Stake (coins)", min_value=0.0, value=10.0, step=10.0, format="%.2f",
                            key="bet_dialog_stake")
    amount = Decimal(f"{stake:.2f}")
    st.caption(f"Your balance: {style.money(user.balance)} coins")
    style.show(f'<div class="ci-muted">{style.esc(_preview(summary, side, amount))}</div>')
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="bet_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Confirm bet", key="bet_confirm", type="primary", width="stretch"):
        attempt(lambda: service.place_bet(user.id, event.id, side, amount, _now()),
                f"Bet placed: {style.money(amount)} on {style.plain(label)}.")
        st.rerun()


def _odds_buttons(user: User, summary: EventSummary) -> None:
    """Draw the two odds boxes of an open yes/no or over/under event as buttons."""
    event = summary.event
    with st.container(key=f"odds_{event.id}", horizontal=True, gap="small",
                      horizontal_alignment="right"):
        for side, odds in ((SIDE_YES, summary.odds_yes), (SIDE_NO, summary.odds_no)):
            number = f"{odds:.2f}" if odds else "—"
            if st.button(f"**{number}** {_short(event, side)}", key=f"odd{side}_{event.id}",
                         help=f"Bet on {betting.side_label(event, side)}"):
                _bet_dialog(user, summary, side)


def _option_rows(user: User, summary: EventSummary) -> None:
    """Draw each name of an open names event as its usual row, made clickable.

    The row is the same HTML as before (name, bar, share, odds); an invisible
    button laid over it (style.py, "optrow_") opens the bet window.
    """
    event = summary.event
    up_to = event.multi_winner
    note = "No bets yet" if summary.pool == 0 else ""
    if note:
        style.show(f'<div class="ci-split"><span class="ci-split-mid">{note}</span></div>')
    for option in _ranked_options(summary):
        members = len({b.username for b in summary.bettors if b.side == option.id})
        row = style.option_row(option.name, stats.side_odds(summary, option.id), option.share,
                               option.stake, members, option.my_stake, up_to, option.won)
        with st.container(key=f"optrow_{event.id}_{option.id}"):
            style.show(f'<div class="ci-opts">{row}</div>')
            if st.button(f"Bet on {style.plain(option.name)}", key=f"optbtn_{event.id}_{option.id}"):
                _bet_dialog(user, summary, option.id)


def _open_card(user: User, summary: EventSummary, records: List[BetRecord], biggest: bool,
               now: datetime) -> None:
    """Draw the top of an open event: the odds or names are buttons that open the bet window."""
    event = summary.event
    if betting.is_names_event(event):
        style.show(f'<div class="ci-ev">{_head_main(user, summary, records, biggest, now)}</div>')
        _option_rows(user, summary)
        return
    main_col, odds_col = st.columns([2.6, 1.6], vertical_alignment="top")
    style.show(where=main_col, markup=_head_main(user, summary, records, biggest, now))
    with odds_col:
        _odds_buttons(user, summary)
    style.show(style.stake_bar(summary.stake_yes, summary.stake_no, labels=_labels(event)))


def _event_item(user: User, summary: EventSummary, records: List[BetRecord],
                now: datetime, biggest: bool) -> None:
    """Draw one event; one waiting for its result gets the dashed orange frame."""
    event = summary.event
    prefix = "wait" if event.status == STATUS_CLOSED else "ev"
    with st.container(key=f"{prefix}_{event.id}"):
        if event.status in (STATUS_OPEN, STATUS_PAUSED):
            _actions_menu(user, summary, now)  # placed in the top-right corner (style.py)
        if event.status == STATUS_OPEN:
            _open_card(user, summary, records, biggest, now)
            _countdown(event.close_time)
            _flag_line(user, summary)
        else:
            style.show(_event_head(user, summary, records, biggest, now))
        names = betting.is_names_event(event)
        if event.status == STATUS_PAUSED:
            _poll_panel(user, summary)
        elif event.status == STATUS_CLOSED and _result_due(event, now) and _may_resolve(user, summary):
            if names:
                _names_resolve_row(user, summary)
            elif betting.is_over_under(event):
                _over_under_resolve_row(user, summary)
            else:
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
    odds = stats.side_odds(summary, record.bet.side) if summary else None
    side = record.winner_label or (record.outcome or "").upper()
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
        f'<div class="ci-card ci-standing"><div class="ci-kicker">Your standing</div>'
        f'<div style="margin-top:8px"><span class="ci-big">{style.rolling(f"#{mine.rank}")}</span> '
        f'<span class="ci-muted">of {len(rows)} · {style.money(mine.balance)} coins</span></div>'
        + "".join(lines) + "</div>"
    )


def board_rows(page: PageData) -> str:
    """Return the compact leaderboard lines with medal ranks; they shuffle into order on load."""
    lines = []
    count = len(page.leaderboard)
    for index, row in enumerate(page.leaderboard):
        is_me = row.username == page.user.username
        extra = " ci-board-me" if is_me else ""
        lines.append(
            f'<div class="ci-board-row ci-shuffle{extra}" style="{style.shuffle_style(index, count)}">'
            f"{style.rank_badge(row.rank)}"
            f"{style.who(row.username, is_me, row.is_admin)}<b>{style.money(row.balance)}</b></div>"
        )
    return "".join(lines)


def _nudge_card(page: PageData) -> str:
    """Return the black 'You vs the group' card for an open event the user skipped."""
    found = stats.nudge(page.events, page.user.username)
    if found is None:
        return ""
    side = style.esc(found.label)
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
    """Return confetti and the coins won for wins announced since the user last saw them, else ''.

    Each win plays once: its notification is marked read before the confetti is
    shown, so it does not come back on the next click, login or device. Several
    new wins show their total. ?confetti in the address plays it once on demand
    (with the latest win's amount, or a sample), for the demo.
    """
    if CONFETTI_PARAM in st.query_params:
        del st.query_params[CONFETTI_PARAM]  # so it plays one time, not on every click
        return confetti.markup(_amount_won(page) or style.signed(DEMO_WIN))
    if not page.new_wins:
        return ""
    try:
        service.mark_wins_seen(page.user.id, [win.notification_id for win in page.new_wins])
    except StorageError:
        pass  # still celebrate; it may play once more next time
    return confetti.markup(style.signed(sum(win.amount for win in page.new_wins)))


@st.fragment(run_every=WIN_CHECK_SECONDS)
def _watch_for_wins(user_id: str) -> None:
    """While the Feed is open, check every WIN_CHECK_SECONDS whether a result made the user a winner.

    If so, the whole page reloads and shows the confetti, so a winner who is
    already looking at the app gets it without clicking. Right after a full
    page load the check is skipped: that load has just looked for wins itself.
    """
    if clock.monotonic() - st.session_state.get(LAST_LOAD_KEY, 0.0) < WIN_CHECK_SECONDS - 1:
        return
    try:
        new_win = service.has_new_win(user_id)
    except StorageError:
        return
    if new_win:
        st.rerun()


# Icon and color of each feed tab (see style.tab_colors).
TAB_ICONS = {
    "All": ("apps", "yellow"),
    "Hot bets": ("local_fire_department", "orange"),
    "Open": ("lock_open", "green"),
    "Closing soon": ("timer", "red"),
    "Awaiting result": ("hourglass_top", "blue"),
    "Done": ("check_circle", "green"),
    "Past bets": ("history", "grey"),
}


def _tab_label(choice: str) -> str:
    """Return a feed tab's label with its icon, e.g. a fire before 'Hot bets'."""
    return style.icon_label(TAB_ICONS.get(choice, ("", ""))[0], choice)


def events_page(page: PageData, me: MemberStats, now: datetime) -> None:
    """Draw the Feed: group name, win banner, filter tabs and events; cards on the right."""
    style.show(style.heading(page.group.name, _feed_kicker(page.events)))
    left, right = st.columns([2.5, 1], gap="large")
    with left:
        if page.user.balance <= 0:
            st.info(auth.TOP_UP_REQUIRED + " The admin tops up everyone in the Group tab.")
        style.show(_confetti(page))
        st.session_state[LAST_LOAD_KEY] = clock.monotonic()
        _watch_for_wins(page.user.id)
        style.show(_win_banner(page, me))
        if st.session_state.get(FILTER_KEY) not in stats.FEED_FILTERS:  # e.g. the old "All"
            st.session_state.pop(FILTER_KEY, None)
        style.tab_colors(FILTER_KEY, [TAB_ICONS[f][1] for f in stats.FEED_FILTERS])
        choice = st.radio("Show", stats.FEED_FILTERS, horizontal=True, key=FILTER_KEY,
                          label_visibility="collapsed", format_func=_tab_label)
        shown = stats.filter_events(page.events, choice, now, len(page.leaderboard))
        if choice != "Hot bets":
            shown = stats.feed_order(shown)
        if not shown and choice == "Hot bets":
            st.info("No hot bets right now. A bet gets hot once half the group has joined it.")
        elif not shown and choice == "Open":
            st.info("No open bets right now. Press New event at the top to start one.")
        elif not shown:
            st.info(f"Nothing in “{choice}” right now.")
        mine = bets_by_event(page.my_bets)
        biggest = _biggest_pool_id(page.events)
        for summary in shown:
            _event_item(page.user, summary, mine.get(summary.event.id, []), now,
                        summary.event.id == biggest)
    with right:
        _side_column(page, me)


@st.cache_data(ttl=60, show_spinner=False)
def _member_names(user_id: str, group_id: Optional[str]) -> List[str]:
    """Return the usernames in the chosen group (kept for a minute, so the dialog stays quick)."""
    return service.group_member_names(user_id, group_id=group_id)


def _typed_names(text: str) -> List[str]:
    """Return the names typed one per line, without blank lines."""
    return [line for line in (text or "").splitlines() if line.strip()]


def _moment_fields(label: str, key: str, today: date, hour: time) -> datetime:
    """Draw a day (tomorrow at first) and a Lisbon time side by side; return them as one moment."""
    day_col, time_col = st.columns(2)
    picked_day: date = day_col.date_input(label, min_value=today, value=today + timedelta(days=1),
                                          key=f"{key}_day")
    picked_time: time = time_col.time_input("At (Lisbon time)", value=hour, key=f"{key}_time")
    return datetime.combine(picked_day, picked_time, tzinfo=style.APP_TIMEZONE)


@st.dialog("New event")
def new_event_dialog(user: User, now: datetime) -> None:
    """Show the pop-up that creates a new event: yes / no, names, or over / under.

    It has no st.form: a form only updates when it is sent, but the Custom
    close time fields must appear as soon as Custom is picked. The dialog is a
    fragment, so a click in it reruns only the pop-up. The keys keep what was
    typed when the type changes.
    """
    today = now.astimezone(style.APP_TIMEZONE).date()
    with st.container(key="choice_kind"):  # drawn as equal round buttons (style.py)
        kind = st.radio("Type", KIND_CHOICES, horizontal=True, key="new_event_kind")
    names_event = kind == KIND_CHOICES[1]
    over_under = kind == KIND_CHOICES[2]
    example = ("Who is going to show up late?" if names_event else
               "Goals in Sunday's derby" if over_under else "Will Ben finish the marathon?")
    title = st.text_input("Question", max_chars=service.MAX_TITLE_LENGTH, placeholder=example,
                          key="new_event_title")
    description = st.text_area("Details (optional)", key="new_event_details",
                               max_chars=service.MAX_DESCRIPTION_LENGTH, height=80)
    if names_event:
        with st.container(key="choice_mode"):
            mode = st.radio("Who can win", WINNER_CHOICES, horizontal=True, help=SEVERAL_HELP,
                            key="new_event_mode")
        picked = st.pills("Add group members", _member_names(user.id, user.group_id), selection_mode="multi",
                          format_func=style.plain, key="new_event_members") or []
        typed = st.text_area("Other names, one per line", height=90, key="new_event_names",
                             placeholder="People who are not in the app")
        st.caption("2 to 10 names. They can't be changed after the event is created.")
    if over_under:
        line = st.number_input("Line", value=2.5, step=0.5, format="%.2f", key="new_event_line",
                               help="Members bet over or under this number. Choose a half "
                                    "(like 2.5) to avoid a result exactly on the line, "
                                    "which refunds everyone.")
        st.caption("The line can't be changed after the event is created.")
    event_time = _moment_fields("Event date", "new_event_date", today, time(18, 0))
    close_col, custom_col = st.columns([1, 2.2], vertical_alignment="bottom")
    choice = close_col.selectbox("Betting closes", betting.CLOSE_CHOICES, key="new_event_closes",
                                 help=CLOSES_HELP)
    custom = None
    if choice == betting.CLOSE_CUSTOM:
        with custom_col, st.container(border=True, key="custom_close"):
            custom = _moment_fields("Betting closes on", "new_event_custom", today, time(17, 0))
    if st.button("Create event", type="primary", width="stretch", key="new_event_create"):
        options = list(picked) + _typed_names(typed) if names_event else None
        several = names_event and mode == WINNER_CHOICES[1]
        line_value = Decimal(f"{line:.2f}") if over_under else None

        def create() -> None:
            closes = betting.close_time_for(event_time, choice, custom)
            service.create_event(user.id, title, closes, _now(), description, options=options,
                                 multi_winner=several, line=line_value, event_time=event_time,
                                 group_id=user.group_id)  # the group the user is looking at

        if attempt(create, "Event created."):
            st.rerun()
        # Keep the dialog open so nothing typed is lost. The message can quote a typed
        # name, so Markdown in it is escaped.
        st.error(style.plain(st.session_state.pop(FLASH_KEY, ("error", ""))[1]))

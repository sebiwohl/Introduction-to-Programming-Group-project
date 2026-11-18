"""The My bets page: four stat tiles and the history of the user's bets."""

from collections import Counter
from typing import Dict, List

import streamlit as st

from src import stats
from src.models import KIND_NAMES
from src.service import BetRecord, EventSummary, PageData
from src.stats import MemberStats
from src.ui import style

FILTERS = ("All", "Open", "Awaiting result", "Won", "Lost", "Refunded")
FILTER_RESULTS = {"Open": (stats.OPEN, stats.PAUSED), "Awaiting result": (stats.AWAITING,),
                  "Won": (stats.WON,), "Lost": (stats.LOST,), "Refunded": (stats.REFUNDED,)}
BADGES = {stats.OPEN: ("Open", "open"), stats.PAUSED: ("Paused", "paused"),
          stats.AWAITING: ("Awaiting result", "waiting"), stats.WON: ("Won", "won"),
          stats.LOST: ("Lost", "lost"), stats.REFUNDED: ("Refunded", "refunded")}


def _plural(count: int, word: str) -> str:
    """Return '1 bet' or '3 bets'."""
    return f"{count} {word}{'' if count == 1 else 's'}"


def _kicker(results: Counter, total: int) -> str:
    """Return the line under the title, like '10 bets · 4 won · 4 lost'."""
    names = ((stats.WON, "won"), (stats.LOST, "lost"), (stats.REFUNDED, "refunded"),
             (stats.AWAITING, "awaiting result"), (stats.OPEN, "open"))
    parts = [_plural(total, "bet")] + [f"{results[r]} {label}" for r, label in names if results[r]]
    return " · ".join(parts)


def _tiles(page: PageData, me: MemberStats, results: Counter) -> str:
    """Return the Balance, In play, Hit rate and Best call tiles."""
    start = page.group.start_balance
    change = page.user.balance - start
    since = f"{style.signed(change)} since you started with {style.money(start)}"
    waiting, open_count = results[stats.AWAITING], results[stats.OPEN] + results[stats.PAUSED]
    play_parts = ([_plural(open_count, "open bet")] if open_count else []) + (
        [f"{_plural(waiting, 'bet')} awaiting result"] if waiting else [])
    play_note = " · ".join(play_parts) or "Nothing riding right now"
    if me.hit_rate is None:
        hit, hit_note = "—", "No settled calls yet"
    else:
        hit, hit_note = f"{round(me.hit_rate * 100)}%", f"{me.right} of {me.settled} settled calls right"
    best = style.odds_text(me.best_odds)
    best_note = style.esc(me.best_title) if me.best_odds else "Win a bet to set one"
    return (
        '<div class="ci-tiles">'
        f'<div class="ci-tile ci-tile-dark"><div class="ci-kicker">Balance</div>'
        f'<div class="ci-big">{style.money(page.user.balance)}</div>'
        f'<div class="ci-muted">{since}</div></div>'
        f'<div class="ci-tile ci-tile-warm"><div class="ci-kicker">In play</div>'
        f'<div class="ci-big">{style.money(stats.in_play(page.my_bets))}</div>'
        f'<div class="ci-muted">{play_note}</div></div>'
        f'<div class="ci-tile"><div class="ci-kicker">Hit rate</div><div class="ci-big">{hit}</div>'
        f'<div class="ci-muted">{hit_note}</div></div>'
        f'<div class="ci-tile"><div class="ci-kicker">Best call</div>'
        f'<div class="ci-big ci-yes">{best}</div><div class="ci-muted">{best_note}</div></div>'
        "</div>"
    )


def _when_line(record: BetRecord, summary) -> str:
    """Return the small date line under an event title."""
    if summary is None:
        return "Deleted"
    verb = {stats.OPEN: "Closes", stats.PAUSED: "Closes"}.get(stats.bet_result(record), "Closed")
    return f"{verb} {style.day(summary.event.close_time)}"


def _status_cell(record: BetRecord, result: str, summary) -> str:
    """Return the badge and the small explanation in the Status column."""
    text, kind = BADGES[result]
    if result == stats.WON:
        note = f"Paid out {style.money(record.bet.payout)}"
    elif result == stats.LOST:
        note = f"{record.winner_label or (record.outcome or '').upper()} won"
    elif result == stats.REFUNDED:
        note = "Stake returned"
    elif result == stats.AWAITING and summary is not None:
        note = f"Waiting for {summary.creator_name} to set it"
    else:
        note = "Betting still open" if result == stats.OPEN else "Betting paused"
    return f'{style.pill(text, kind)}<div class="ci-muted" style="font-size:.75rem">{style.esc(note)}</div>'


def _coins_cell(record: BetRecord, result: str, summary) -> str:
    """Return the coins won or lost, or the estimate while the bet is not settled."""
    bet = record.bet
    if result in (stats.WON, stats.LOST, stats.REFUNDED):
        change = (bet.payout if bet.payout is not None else bet.stake) - bet.stake
        css = "ci-gain" if change > 0 else "ci-loss"
        return f'<span class="{css}">{style.signed(change)}</span>'
    profit = stats.estimated_profit(bet.stake, summary, bet.side) if summary else None
    if profit is None:
        return '<span class="ci-muted">—</span>'
    up_to = "up to " if summary.event.multi_winner else ""
    return (f'<span class="ci-muted">≈ {up_to}{style.signed(profit)} if '
            f'{style.esc(_label(record))}</span>')


def _label(record: BetRecord) -> str:
    """Return how the bet's side is shown: YES, NO or the option's name."""
    return record.side_label or record.bet.side.upper()


def _call_tag(record: BetRecord) -> str:
    """Return the side tag: blue YES or orange-red NO, or the option's name in bold."""
    if record.kind != KIND_NAMES:
        return style.side_tag(record.bet.side)
    return f"<b>{style.esc(_label(record))}</b>"


def _history_table(records: List[BetRecord], by_id: Dict[str, EventSummary]) -> str:
    """Return the history table, one row per bet."""
    rows = []
    for record in records:
        summary = by_id.get(record.bet.event_id)
        result = stats.bet_result(record)
        known = summary is not None and result != stats.REFUNDED
        odds = stats.side_odds(summary, record.bet.side) if known else None
        warm = ' class="ci-warm"' if result == stats.AWAITING else ""
        rows.append(
            f"<tr{warm}><td><div class='ci-t-title'>{style.esc(record.event_title)}</div>"
            f'<div class="ci-muted" style="font-size:.75rem">{_when_line(record, summary)}</div></td>'
            f"<td>{_call_tag(record)}&nbsp; <b>{style.money(record.bet.stake)}</b></td>"
            f"<td><b>{style.odds_text(odds)}</b></td>"
            f"<td>{_status_cell(record, result, summary)}</td>"
            f'<td class="ci-num">{_coins_cell(record, result, summary)}</td></tr>'
        )
    head = ("<tr><th>Event</th><th>Your call</th><th>Odds</th><th>Status</th>"
            '<th class="ci-num">Coins</th></tr>')
    return f'<div class="ci-table-wrap"><table class="ci-table">{head}{"".join(rows)}</table></div>'


def my_bets_page(page: PageData, me: MemberStats) -> None:
    """Draw the My bets page."""
    results = Counter(stats.bet_result(r) for r in page.my_bets)
    style.show(style.heading("My bets", _kicker(results, len(page.my_bets))))
    style.show(_tiles(page, me, results))
    label_col, filter_col = st.columns([1, 3], vertical_alignment="center")
    style.show(where=label_col, markup='<div class="ci-label">History</div>')
    with filter_col:
        choice = st.radio("Filter", FILTERS, horizontal=True, key="pills_history",
                          label_visibility="collapsed")
    wanted = FILTER_RESULTS.get(choice)
    records = [r for r in page.my_bets if wanted is None or stats.bet_result(r) in wanted]
    if not records:
        st.info("No bets here yet." if choice == "All" else f"No bets in “{choice}”.")
        return
    by_id = {s.event.id: s for s in page.events}
    style.show(_history_table(records, by_id))


"""The Leaderboard page: top three, the full ranking, head to head and group records."""

from typing import Dict, List, Optional

import streamlit as st

from src import stats
from src.service import LeaderboardRow, PageData
from src.stats import MemberStats
from src.ui import style


# Icon and color of each "Rank by" tab (see style.tab_colors).
RANK_ICONS = {"Coins": ("toll", "gold"), "Hit rate": ("my_location", "green"),
              "Best call": ("bolt", "orange"), "Streak": ("local_fire_department", "red")}


def _hit_rate(member: MemberStats) -> str:
    """Return the hit rate like '70% · 7/10', or a dash without settled calls."""
    if member.hit_rate is None:
        return "—"
    return f"{round(member.hit_rate * 100)}% · {member.right}/{member.settled}"


def _streak(member: MemberStats) -> str:
    """Return the streak like 'W3' (blue) or 'L1', or a dash."""
    if member.streak > 0:
        return f'<b class="ci-yes">W{member.streak}</b>'
    if member.streak < 0:
        return f"<b>L{-member.streak}</b>"
    return "—"


def _admin_name(rows: List[LeaderboardRow]) -> str:
    """Return the admin's username, or an empty string."""
    return next((r.username for r in rows if r.is_admin), "")


def _kicker(page: PageData) -> str:
    """Return the line under the title: group, members, admin."""
    admin = _admin_name(page.leaderboard)
    parts = [page.group.name, f"{len(page.leaderboard)} members"]
    if admin:
        parts.append(f"admin: {admin}")
    return " · ".join(parts)


def _podium(page: PageData, member: Dict[str, MemberStats]) -> str:
    """Return the three cards for the top of the ranking by coins."""
    cards = []
    for index, row in enumerate(page.leaderboard[:3]):
        is_me = row.username == page.user.username
        kind = " ci-podium-first" if index == 0 else (" ci-podium-me" if is_me else "")
        name = "You" if is_me else style.esc(row.username)
        rate = member.get(row.username, MemberStats()).hit_rate
        detail = f"{style.money(row.balance)} coins"
        if rate is not None:
            detail += f" · {round(rate * 100)}% hit rate"
        cards.append(
            f'<div class="ci-podium-card{kind}" style="--t:{index}"><div class="ci-row">'
            f'<span class="ci-podium-rank">{style.rolling(str(row.rank))}</span>'
            f'{style.avatar(row.username, is_me)}</div>'
            f'<div class="ci-podium-name">{name}</div><div class="ci-muted">{detail}</div></div>'
        )
    return f'<div class="ci-podium">{"".join(cards)}</div>'


def _table(page: PageData, ordered: List[LeaderboardRow], member: Dict[str, MemberStats],
           ranking: str) -> str:
    """Return the ranking table; # is the coin rank, or the place in the chosen ranking.

    The rows shuffle into order, again each time the ranking changes.
    """
    by_coins = ranking == "Coins"
    rows = []
    count = len(ordered)
    variant = stats.RANKINGS.index(ranking) if ranking in stats.RANKINGS else 0
    for place, row in enumerate(ordered, start=1):
        is_me = row.username == page.user.username
        stats_of = member.get(row.username, MemberStats())
        change = row.balance - row.received
        change_css = "ci-gain" if change > 0 else ""
        rows.append(
            f'<tr class="ci-shuffle{" ci-me" if is_me else ""}" '
            f'style="{style.shuffle_style(place - 1, count, variant)}">'
            f"<td>{style.rank_badge(row.rank if by_coins else place)}</td>"
            f"<td>{style.who(row.username, is_me, row.is_admin)}</td>"
            f"<td><b>{style.money(row.balance)}</b></td>"
            f'<td><span class="{change_css}">{style.signed(change)}</span></td>'
            f"<td>{_hit_rate(stats_of)}</td><td>{_streak(stats_of)}</td>"
            f"<td><b>{style.odds_text(stats_of.best_odds)}</b></td></tr>"
        )
    head = ("<tr><th>#</th><th>Member</th><th>Coins</th><th>This period</th><th>Hit rate</th>"
            "<th>Streak</th><th>Best call</th></tr>")
    return f'<div class="ci-table-wrap"><table class="ci-table">{head}{"".join(rows)}</table></div>'


def _compare(mine, theirs) -> tuple:
    """Return the CSS classes for two values: the higher one is highlighted."""
    if mine is None or theirs is None or mine == theirs:
        return "", ""
    return ("ci-better", "") if mine > theirs else ("", "ci-better")


def _head_to_head(page: PageData, member: Dict[str, MemberStats]) -> str:
    """Return the card comparing the user with the closest rival on the board."""
    rows = page.leaderboard
    rival = stats.closest_rival(rows, page.user.username)
    mine = next((r for r in rows if r.username == page.user.username), None)
    if rival is None or mine is None:
        return ""
    me_s = member.get(mine.username, MemberStats())
    them = member.get(rival.username, MemberStats())
    lines = [
        ("Coins", mine.balance, rival.balance, style.money(mine.balance), style.money(rival.balance)),
        ("Hit rate", me_s.hit_rate, them.hit_rate, _hit_rate(me_s).split(" ")[0],
         _hit_rate(them).split(" ")[0]),
        ("Best call", me_s.best_odds, them.best_odds, style.odds_text(me_s.best_odds),
         style.odds_text(them.best_odds)),
        ("Streak", me_s.streak if me_s.settled else None, them.streak if them.settled else None,
         _streak(me_s), _streak(them)),
    ]
    cells = []
    for label, a, b, a_text, b_text in lines:
        a_css, b_css = _compare(a, b)
        cells.append(f'<span class="{a_css}">{a_text}</span><span class="ci-kicker">{label}</span>'
                     f'<span class="ci-right {b_css}">{b_text}</span>')
    gap = rival.balance - mine.balance
    if gap > 0:
        note = f"Win more than {style.money(gap)} coins to take #{rival.rank} from {style.esc(rival.username)}."
    elif gap < 0:
        note = f"You lead {style.esc(rival.username)} by {style.money(-gap)} coins."
    else:
        note = f"You and {style.esc(rival.username)} are level."
    header = (
        f'<div class="ci-row" style="margin:12px 0 14px">{style.who(mine.username, True)}'
        f'<span class="ci-kicker">vs</span><span class="ci-who"><b>{style.esc(rival.username)}</b>'
        f"{style.avatar(rival.username)}</span></div>"
    )
    return (
        f'<div class="ci-card"><div class="ci-row"><span class="ci-label">Head to head</span>'
        f'<span class="ci-muted">Your closest rival</span></div>{header}'
        f'<div class="ci-h2h">{"".join(cells)}</div><div class="ci-note">{note}</div></div>'
    )


def _record_line(label: str, holder: Optional[str], value: str, user: str) -> str:
    """Return one line of the group records card."""
    if holder is None:
        return (f'<div class="ci-member"><span class="ci-label">{label}</span>'
                f'<span class="ci-member-money ci-muted">Nobody yet</span></div>')
    return (f'<div class="ci-member"><span class="ci-label">{label}</span>'
            f'<span class="ci-member-money">{style.who(holder, holder == user)}'
            f"&nbsp; <b>{value}</b></span></div>")


def _records(page: PageData, member: Dict[str, MemberStats]) -> str:
    """Return the card with the group's best hit rate, best call and longest win streak."""
    rated = {n: m for n, m in member.items() if m.settled}
    best_rate = max(rated, key=lambda n: rated[n].hit_rate, default=None)
    with_odds = {n: m for n, m in rated.items() if m.best_odds}
    best_call = max(with_odds, key=lambda n: with_odds[n].best_odds, default=None)
    streaks = {n: m for n, m in rated.items() if m.streak > 0}
    hot = max(streaks, key=lambda n: streaks[n].streak, default=None)
    me = page.user.username
    lines = [
        _record_line("Best hit rate", best_rate,
                     _hit_rate(rated[best_rate]) if best_rate else "", me),
        _record_line("Best call", best_call,
                     style.odds_text(with_odds[best_call].best_odds) if best_call else "", me),
        _record_line("Hottest streak", hot, f"W{streaks[hot].streak}" if hot else "", me),
    ]
    return (f'<div class="ci-card"><div class="ci-label" style="margin-bottom:6px">Group records</div>'
            f'{"".join(lines)}<div class="ci-muted" style="margin-top:10px">Worked out from '
            "resolved events. Refunded bets don't count.</div></div>")


def leaderboard_page(page: PageData, member: Dict[str, MemberStats]) -> None:
    """Draw the Leaderboard page."""
    style.show(style.heading("Leaderboard", _kicker(page)))
    style.show(_podium(page, member))
    style.show('<div class="ci-label" style="margin-bottom:4px">Rank by</div>')
    style.tab_colors("tabs_rank", [RANK_ICONS[r][1] for r in stats.RANKINGS])
    ranking = st.radio("Rank by", stats.RANKINGS, horizontal=True, key="tabs_rank",
                       label_visibility="collapsed",
                       format_func=lambda r: style.icon_label(RANK_ICONS[r][0], r))
    ordered = stats.rank_members(page.leaderboard, member, ranking)
    style.show(_table(page, ordered, member, ranking))
    st.write("")
    left, right = st.columns(2, gap="medium")
    style.show(where=left, markup=_head_to_head(page, member))
    style.show(where=right, markup=_records(page, member))

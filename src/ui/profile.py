"""The Profile page: who you are, your coins, your place in the ranking and your betting stats."""

from typing import Optional

from src import stats
from src.models import ROLE_ADMIN
from src.service import LeaderboardRow, PageData
from src.stats import MemberStats, ProfileStats
from src.ui import style


def _tile(label: str, value: str, note: str, extra: str = "") -> str:
    """Return one stat tile: small label, big value, grey note underneath."""
    return (f'<div class="ci-tile{extra}"><div class="ci-kicker">{label}</div>'
            f'<div class="ci-big">{value}</div><div class="ci-muted">{note}</div></div>')


def _identity(page: PageData, row: Optional[LeaderboardRow]) -> str:
    """Return the card with the big avatar, username, role and group."""
    user = page.user
    is_admin = (row.is_admin if row is not None else user.role == ROLE_ADMIN)
    badge = style.admin_badge() if is_admin else ""
    role = "Group admin" if is_admin else "Member"
    return (
        f'<div class="ci-card ci-profile">{style.avatar(user.username, True)}'
        f'<div><div class="ci-profile-name">{style.esc(user.username)}{badge}</div>'
        f'<div class="ci-muted">{role} of <b>{style.esc(page.group.name)}</b></div></div></div>'
    )


def _rank_note(page: PageData, row: LeaderboardRow) -> str:
    """Return the line under the ranking tile: the gap to the next place up, or the lead."""
    above = stats.next_above(page.leaderboard, row.username)
    if above is not None:
        gap = above.balance - row.balance
        return f"{style.money(gap)} coins behind {style.esc(above.username)}"
    return "You lead the group"


def _main_tiles(page: PageData, me: MemberStats, profile: ProfileStats,
                row: Optional[LeaderboardRow]) -> str:
    """Return the Coins, Ranking, Bets and Hit rate tiles."""
    start = page.group.start_balance
    change = page.user.balance - start
    coins_note = f"{style.signed(change)} since you started with {style.money(start)}"
    if row is None:
        rank, rank_note = "—", "Not on the leaderboard"
    else:
        rank = f"#{row.rank}"
        rank_note = f"of {len(page.leaderboard)} · {_rank_note(page, row)}"
    parts = [f"{profile.won} won", f"{profile.lost} lost"]
    if profile.refunded:
        parts.append(f"{profile.refunded} refunded")
    if profile.unsettled:
        parts.append(f"{profile.unsettled} not settled")
    bets_note = " · ".join(parts) if profile.bets else "No bets yet"
    if me.hit_rate is None:
        hit, hit_note = "—", "No settled calls yet"
    else:
        hit = f"{round(me.hit_rate * 100)}%"
        hit_note = f"{me.right} of {me.settled} settled calls right"
    return (
        '<div class="ci-tiles">'
        + _tile("Coins", style.money(page.user.balance), coins_note, " ci-tile-dark")
        + _tile("Ranking", rank, rank_note, " ci-tile-warm")
        + _tile("Bets placed", str(profile.bets), bets_note)
        + _tile("Hit rate", hit, hit_note)
        + "</div>"
    )


def _plural(count: int, word: str) -> str:
    """Return '1 event' or '3 events'."""
    return f"{count} {word}{'' if count == 1 else 's'}"


def _streak_text(me: MemberStats) -> tuple:
    """Return the streak value and its note, e.g. ('W3', '3 right calls in a row')."""
    if me.streak > 0:
        return f'<span class="ci-yes">W{me.streak}</span>', f"{_plural(me.streak, 'right call')} in a row"
    if me.streak < 0:
        return f"L{-me.streak}", f"{_plural(-me.streak, 'wrong call')} in a row"
    return "—", "No settled calls yet"


def _more_tiles(page: PageData, me: MemberStats, profile: ProfileStats) -> str:
    """Return the Total staked, Biggest win, Best call and Streak tiles."""
    net_css = "ci-gain" if profile.net_won > 0 else ("ci-loss" if profile.net_won < 0 else "")
    staked_note = (f'<span class="{net_css}">{style.signed(profile.net_won)}</span> '
                   "won or lost on settled bets")
    if profile.biggest_win is None:
        win, win_note = "—", "Win a bet to set one"
    else:
        win = f'<span class="ci-yes">+{style.money(profile.biggest_win)}</span>'
        win_note = style.esc(profile.biggest_win_title)
    best_note = style.esc(me.best_title) if me.best_odds else "Highest odds you won at"
    streak, streak_note = _streak_text(me)
    in_play = stats.in_play(page.my_bets)
    created = f"You created {_plural(profile.events_created, 'event')}"
    return (
        '<div class="ci-tiles ci-tiles-more">'
        + _tile("Total staked", style.money(profile.total_staked), staked_note)
        + _tile("Biggest win", win, win_note)
        + _tile("Best call", f'<span class="ci-yes">{style.odds_text(me.best_odds)}</span>', best_note)
        + _tile("Streak", streak, streak_note)
        + "</div>"
        + f'<div class="ci-muted">{style.money(in_play)} coins in play right now · {created}.</div>'
    )


def profile_page(page: PageData, me: MemberStats) -> None:
    """Draw the Profile page."""
    row = stats.my_row(page.leaderboard, page.user.username)
    profile = stats.profile_stats(page.my_bets, page.events, page.user.id)
    style.show(style.heading("Profile", "Your account and your record in the group"))
    style.show(_identity(page, row))
    style.show(_main_tiles(page, me, profile, row) + _more_tiles(page, me, profile))

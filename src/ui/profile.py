"""The Profile page: who you are, your coins, your place in the ranking and your betting stats.

The stats are shown for one group at a time (a dropdown above them). At the
bottom you can create or join another group.
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from src import service, stats
from src.betting import BettingError
from src.models import AVATARS, ROLE_ADMIN
from src.service import LeaderboardRow, PageData
from src.stats import MemberStats, ProfileStats
from src.storage import StorageError
from src.ui import style
from src.ui.account import create_group_form, join_group_form, log_out
from src.ui.actions import attempt

STATS_GROUP_KEY = "profile_stats_group"  # the group the Profile stats are shown for
PERIOD_KEY = "profile_stats_period"  # Since last repayment / All time


def _tile(order: int, label: str, value: str, note: str, extra: str = "") -> str:
    """Return one stat tile: small label, big rolling value, grey note underneath.

    order (0, 1, 2 ...) staggers the tile's entrance and its rolling digits.
    """
    return (f'<div class="ci-tile{extra}" style="--t:{order}"><div class="ci-kicker">{label}</div>'
            f'{style.big_number(value)}<div class="ci-muted">{note}</div></div>')


EDITING_KEY = "editing_profile"
NO_ANIMAL = "🚫"  # the picker's "No animal" button


def _identity(page: PageData, row: Optional[LeaderboardRow]) -> str:
    """Return the big picture, username, role and group (the inside of the profile card)."""
    user = page.user
    is_admin = (row.is_admin if row is not None else user.role == ROLE_ADMIN)
    badge = style.admin_badge() if is_admin else ""
    role = "Group admin" if is_admin else "Member"
    return (
        f'<div class="ci-profile">{style.avatar(user.username, True)}'
        f'<div><div class="ci-profile-name">{style.esc(user.username)}{badge}</div>'
        f'<div class="ci-muted">{role} of <b>{style.esc(page.group.name)}</b></div></div></div>'
    )


def _toggle_editing() -> None:
    """Open or close the profile editor."""
    st.session_state[EDITING_KEY] = not st.session_state.get(EDITING_KEY, False)


def _pick(user_id: str, animal: str) -> None:
    """Save the chosen animal ("" for none) as the profile picture and close the editor if it worked."""
    message = "Profile picture updated." if animal else "Profile picture removed."
    if attempt(lambda: service.set_avatar(user_id, animal), message):
        st.session_state[EDITING_KEY] = False


def _avatar_picker(page: PageData) -> None:
    """Draw "No animal" and the seven animal pictures; clicking one saves it as the profile picture."""
    st.markdown('<div class="ci-label" style="margin:14px 0 6px">Choose your profile picture</div>',
                unsafe_allow_html=True)
    choices = ("",) + AVATARS  # "" = no animal: the round initial is shown instead
    with st.container(key="avatars"):
        columns = st.columns(len(choices))
        for column, animal in zip(columns, choices):
            chosen = page.user.avatar == animal
            column.button(
                style.ANIMALS.get(animal, NO_ANIMAL), key=f"avatar_{animal or 'none'}",
                type="primary" if chosen else "secondary", width="stretch",
                on_click=_pick, args=(page.user.id, animal),
            )


def _profile_card(page: PageData, row: Optional[LeaderboardRow]) -> None:
    """Draw the profile card with Edit profile and Log out in the corner, and the editor if open."""
    editing = st.session_state.get(EDITING_KEY, False)
    with st.container(key="card_profile"):
        info_col, button_col = st.columns([4, 1])
        style.show(where=info_col, markup=_identity(page, row))
        button_col.button("Close" if editing else "Edit profile", key="edit_profile",
                          icon=":material/close:" if editing else ":material/edit:",
                          width="stretch", on_click=_toggle_editing)
        if button_col.button("Log out", key="logout_profile", icon=":material/logout:",
                             width="stretch"):
            log_out()
        if editing:
            _avatar_picker(page)


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
    mine = next((r for r in page.leaderboard if r.username == page.user.username), None)
    change = mine.result if mine else page.user.balance - page.user.received
    coins_note = (f"{style.signed(change)} since last repayment · "
                  f"received {style.money(page.user.received)}")
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
        '<div class="ci-tiles ci-tiles-anim">'
        + _tile(0, "Coins", style.money(page.user.balance), coins_note, " ci-tile-dark")
        + _tile(1, "Ranking", rank, rank_note, " ci-tile-warm")
        + _tile(2, "Bets placed", str(profile.bets), bets_note)
        + _tile(3, "Hit rate", hit, hit_note)
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
        '<div class="ci-tiles ci-tiles-more ci-tiles-anim">'
        + _tile(4, "Total staked", style.money(profile.total_staked), staked_note)
        + _tile(5, "Biggest win", win, win_note)
        + _tile(6, "Best call", f'<span class="ci-yes">{style.odds_text(me.best_odds)}</span>', best_note)
        + _tile(7, "Streak", streak, streak_note)
        + "</div>"
        + f'<div class="ci-muted">{style.money(in_play)} coins in play right now · {created}.</div>'
    )


def _stats_choice(page: PageData) -> tuple:
    """Draw the group dropdown and the period switch above the stats.

    Returns (page data for the chosen group, chosen period). The group starts
    on the one shown everywhere else; picking another loads that group's
    figures once (the rest of the app stays on its group).
    """
    names = {group.id: group.name for group in page.groups}
    if st.session_state.get(STATS_GROUP_KEY) not in names:
        st.session_state[STATS_GROUP_KEY] = page.group.id
    label_col, box_col, period_col = st.columns([1, 2.6, 5], vertical_alignment="center")
    style.show(where=label_col, markup='<div class="ci-label">Stats for</div>')
    chosen = box_col.selectbox("Stats for", list(names), key=STATS_GROUP_KEY,
                               format_func=names.get, label_visibility="collapsed")
    period = period_col.radio("Period", stats.PERIODS, horizontal=True, key=PERIOD_KEY,
                              label_visibility="collapsed",
                              help="Since last repayment: only events whose betting closed "
                                   "after the last repayment count.")
    if chosen == page.group.id:
        return page, period
    try:
        return service.page_data(page.user.id, datetime.now(timezone.utc), group_id=chosen), period
    except (BettingError, StorageError) as error:
        st.error(str(error))
        return page, period


def _more_groups(page: PageData) -> None:
    """Draw the forms to create or join another group (a user can be in several), always open."""
    style.show('<div class="ci-label" style="margin-top:8px">Create or join another group</div>')
    st.caption("You can be in several groups, each with its own balance. "
               "Switch between them with the Group dropdown at the top.")
    create_col, join_col = st.columns(2, gap="medium")
    with create_col:
        create_group_form(page.user)
    with join_col:
        join_group_form(page.user)


def profile_page(page: PageData, me: MemberStats) -> None:
    """Draw the Profile page: the card, then the stats of the chosen group, then more groups."""
    style.show(style.heading("Profile", "Your account and your record in each group"))
    st.write("")
    _profile_card(page, stats.my_row(page.leaderboard, page.user.username))
    st.write("")
    shown, period = _stats_choice(page)
    # only this group's bets and the chosen period, so every tile counts the same ones
    group_bets = [r for r in shown.my_bets if r.group_id == shown.group.id]
    shown = replace(shown,
                    my_bets=stats.period_records(group_bets, period, shown.period_start),
                    events=stats.period_events(shown.events, period, shown.period_start))
    me = stats.member_stats(shown.events).get(shown.user.username, MemberStats())
    row = stats.my_row(shown.leaderboard, shown.user.username)
    profile = stats.profile_stats(shown.my_bets, shown.events, shown.user.id)
    style.show(_main_tiles(shown, me, profile, row) + _more_tiles(shown, me, profile))
    st.write("")
    _more_groups(page)

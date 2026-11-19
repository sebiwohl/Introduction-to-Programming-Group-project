"""The top bar: logo, page tabs, streak and balance, the notification bell and New event."""

from typing import Callable

import streamlit as st

from src import notifications, service
from src.service import PageData
from src.stats import MemberStats
from src.ui import style
from src.ui.account import GROUP_KEY
from src.ui.actions import attempt

BELL_SHOWN = 20  # the newest notifications listed in the bell

FEED, MY_BETS, LEADERBOARD, GROUP, PROFILE = "Feed", "My bets", "Leaderboard", "Group", "Profile"
PAGES = (FEED, MY_BETS, LEADERBOARD, GROUP, PROFILE)
NAV_KEY = "nav"


def page_key(page_name: str) -> str:
    """Return the container key for a page, e.g. 'page_2'; each page fades in under its own key."""
    index = PAGES.index(page_name) if page_name in PAGES else 0
    return f"page_{index}"


def go_to(page_name: str) -> None:
    """Switch to another page (use as a button's on_click, before the tabs are drawn)."""
    st.session_state[NAV_KEY] = page_name


def _top_stats(me: MemberStats, page: PageData) -> str:
    """Return the streak (only while winning 2 or more in a row) and the balance."""
    streak = ""
    if me.streak >= 2:
        streak = (f'<span class="ci-streak" title="{me.streak} correct calls in a row">'
                  f"{style.FLAME}{me.streak}</span>")
    coins = f'<span class="ci-coins" title="Your balance">{style.COIN}{style.money(page.user.balance)}</span>'
    return f'<div class="ci-topstats">{streak}{coins}</div>'


def _bell(page: PageData) -> None:
    """Draw the bell: the unread count, the newest notifications and "Mark all as read"."""
    unread = notifications.unread_count(page.notifications)
    with st.popover(str(unread) if unread else " ", icon=":material/notifications:",
                    help="Notifications"):
        if not page.notifications:
            st.caption("No notifications yet.")
            return
        lines = []
        for note in page.notifications[:BELL_SHOWN]:
            weight = "800" if not note.read else "400"
            lines.append(
                f'<div style="padding:6px 0;border-bottom:1px solid var(--ci-line)">'
                f'<div style="font-weight:{weight}">{style.esc(note.text)}</div>'
                f'<div class="ci-muted" style="font-size:.75rem">{style.when(note.created_at)}</div>'
                "</div>"
            )
        style.show("".join(lines))
        if unread and st.button("Mark all as read", key="bell_read_all"):
            attempt(lambda: notifications.mark_all_read(page.user.id,
                                                        skip_prefix=service.WIN_PREFIX))
            st.rerun()


def _group_switcher(page: PageData) -> None:
    """Draw the group dropdown, only for users in more than one group.

    Every page then shows the chosen group: its feed, balance and leaderboard.
    """
    if len(page.groups) < 2:
        return
    names = {group.id: group.name for group in page.groups}
    st.session_state[GROUP_KEY] = page.group.id  # the group actually shown (it may have changed)
    label_col, box_col, _ = st.columns([0.8, 2.6, 6], vertical_alignment="center")
    style.show(where=label_col, markup='<div class="ci-label">Group</div>')
    box_col.selectbox("Group", list(names), key=GROUP_KEY, format_func=names.get,
                      label_visibility="collapsed")


def top_bar(page: PageData, me: MemberStats, on_new_event: Callable[[], None]) -> str:
    """Draw the top bar and return the page the user picked.

    on_new_event runs when the New event button is pressed (it opens the dialog).
    """
    with st.container(key="topbar"):
        logo_col, nav_col, stats_col, bell_col, button_col = st.columns(
            [1.3, 3.6, 0.9, 0.6, 1.5], vertical_alignment="center"
        )
        style.show(where=logo_col, markup=style.logo())
        with nav_col:
            choice = st.radio("Menu", PAGES, horizontal=True, key=NAV_KEY,
                              label_visibility="collapsed")
        style.show(where=stats_col, markup=_top_stats(me, page))
        with bell_col:
            _bell(page)
        if button_col.button("New event", key="new_event", type="primary",
                             icon=":material/add:", width="stretch"):
            on_new_event()
        _group_switcher(page)
    return choice

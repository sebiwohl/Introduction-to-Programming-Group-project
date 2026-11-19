"""The top bar: logo, page tabs, streak and balance, the notification bell and New event.

The page tabs come in two parts. On the left, in one shaded block, the group
dropdown with the pages that show only that group (Feed, Leaderboard, Group).
On the right, apart, the pages about you across all your groups (My bets,
Profile). The two radios act as one menu: NAV_KEY holds the open page.
"""

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
GROUP_PAGES = (FEED, LEADERBOARD, GROUP)  # follow the group dropdown
MY_PAGES = (MY_BETS, PROFILE)  # about the user across all their groups
PAGES = GROUP_PAGES + MY_PAGES
NAV_KEY = "nav"  # the open page (set by either radio, or by go_to)
GROUP_TABS_KEY, MY_TABS_KEY = "nav_group", "nav_me"


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


def _picked(tabs_key: str) -> None:
    """Open the page picked in one of the two tab rows (the other one is cleared on redraw)."""
    if st.session_state.get(tabs_key):
        st.session_state[NAV_KEY] = st.session_state[tabs_key]


def _tabs(key: str, pages: tuple, current: str) -> None:
    """Draw one row of page tabs; it shows nothing selected when the open page is in the other row."""
    st.session_state[key] = current if current in pages else None
    st.radio("Menu", pages, horizontal=True, key=key, label_visibility="collapsed",
             on_change=_picked, args=(key,))


def _group_dropdown(page: PageData) -> None:
    """Draw the group dropdown, styled like the tabs; the pages next to it follow it."""
    names = {group.id: group.name for group in page.groups}
    st.session_state[GROUP_KEY] = page.group.id  # the group actually shown (it may have changed)
    with st.container(key="groupsel"):
        st.selectbox("Group", list(names), key=GROUP_KEY, format_func=names.get,
                     label_visibility="collapsed",
                     help="Feed, Leaderboard and Group show this group. "
                          "My bets and Profile cover all your groups.")


def top_bar(page: PageData, me: MemberStats, on_new_event: Callable[[], None]) -> str:
    """Draw the top bar and return the page the user picked.

    on_new_event runs when the New event button is pressed (it opens the dialog).
    """
    choice = st.session_state.get(NAV_KEY)
    if choice not in PAGES:
        choice = st.session_state[NAV_KEY] = FEED
    with st.container(key="topbar"):
        with st.container(key="toprow", horizontal=True, vertical_alignment="center",
                          gap="small"):
            style.show(style.logo())
            style.show(_top_stats(me, page))
            _bell(page)
            if st.button("New event", key="new_event", type="primary", icon=":material/add:"):
                on_new_event()
        with st.container(key="navrow", horizontal=True, vertical_alignment="center",
                          gap="medium"):
            with st.container(key="navgroup", horizontal=True, vertical_alignment="center",
                              gap="small"):
                _group_dropdown(page)
                _tabs(GROUP_TABS_KEY, GROUP_PAGES, choice)
            with st.container(key="navme"):
                _tabs(MY_TABS_KEY, MY_PAGES, choice)
    return choice

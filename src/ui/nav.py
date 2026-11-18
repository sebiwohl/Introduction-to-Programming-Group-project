"""The top bar: logo, page tabs, streak and balance, and the New event button."""

from typing import Callable

import streamlit as st

from src.service import PageData
from src.stats import MemberStats
from src.ui import style

FEED, MY_BETS, LEADERBOARD, GROUP, PROFILE = "Feed", "My bets", "Leaderboard", "Group", "Profile"
PAGES = (FEED, MY_BETS, LEADERBOARD, GROUP, PROFILE)
NAV_KEY = "nav"


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


def top_bar(page: PageData, me: MemberStats, on_new_event: Callable[[], None]) -> str:
    """Draw the top bar and return the page the user picked.

    on_new_event runs when the New event button is pressed (it opens the dialog).
    """
    with st.container(key="topbar"):
        logo_col, nav_col, stats_col, button_col = st.columns(
            [1.4, 4.0, 1.1, 1.2], vertical_alignment="center"
        )
        style.show(where=logo_col, markup=style.logo())
        with nav_col:
            choice = st.radio("Menu", PAGES, horizontal=True, key=NAV_KEY,
                              label_visibility="collapsed")
        style.show(where=stats_col, markup=_top_stats(me, page))
        if button_col.button("New event", key="new_event", type="primary",
                             icon=":material/add:", width="stretch"):
            on_new_event()
    return choice

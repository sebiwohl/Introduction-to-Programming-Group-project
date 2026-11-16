"""The sidebar and the two smaller pages: Balances and Profile."""

import streamlit as st

from src import service
from src.models import Group, User
from src.ui import style
from src.ui.account import USER_KEY
from src.ui.events import SIDE_LABEL, leaderboard_panel

PAGES = ("Events", "Balances", "Profile")


def _log_out_button(key: str) -> None:
    """Draw a button that forgets the logged-in user."""
    if st.button("Log out", key=key):
        st.session_state.pop(USER_KEY, None)
        st.rerun()


def sidebar(user: User, group: Group) -> str:
    """Draw the sidebar and return the page the user picked."""
    with st.sidebar:
        st.title("Called it!")
        page = st.radio("Menu", PAGES, label_visibility="collapsed")
        with st.container(border=True, key="card_group"):
            st.caption("Group")
            st.markdown(f"**{group.name}**")
            st.caption("Your balance")
            st.markdown(f"**{style.money(user.balance)}**")
            st.caption("Invite code")
            st.markdown(f'<span class="ci-code">{group.invite_code}</span>',
                        unsafe_allow_html=True)
        st.caption(f"Signed in as {user.username}")
        _log_out_button("logout_sidebar")
    return page


def _history_rows(user: User) -> list:
    """Return the user's bets as table rows."""
    rows = []
    for record in service.bet_history(user.id):
        payout = record.bet.payout
        rows.append({
            "Event": record.event_title,
            "Side": SIDE_LABEL[record.bet.side],
            "Stake": style.money(record.bet.stake),
            "Status": record.event_status.capitalize(),
            "Result": SIDE_LABEL.get(record.outcome, "—"),
            "Payout": style.money(payout) if payout is not None else "—",
        })
    return rows


def balances_page(user: User) -> None:
    """Draw the Balances page: own balance, the leaderboard and the bet history."""
    st.header("Balances")
    left, right = st.columns([1, 2], gap="large")
    with left:
        st.metric("Your balance", style.money(user.balance))
        leaderboard_panel(user)
    with right:
        st.subheader("Your bets")
        rows = _history_rows(user)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("You have not placed a bet yet.")


def profile_page(user: User, group: Group) -> None:
    """Draw the Profile page: who you are and which group you are in."""
    st.header("Profile")
    with st.container(border=True, key="card_profile"):
        st.markdown(f"**Username:** {user.username}")
        st.markdown(f"**Role:** {user.role.capitalize()}")
        st.markdown(f"**Group:** {group.name}")
        st.markdown(f"**Invite code:** `{group.invite_code}`")
        st.markdown(f"**Balance:** {style.money(user.balance)}")
    _log_out_button("logout_profile")

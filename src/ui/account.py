"""Screens before the feed: log in, sign up, and create or join a group."""


import streamlit as st

from src import auth
from src.models import User
from src.ui import style
from src.ui.actions import attempt, show_flash

USER_KEY = "user_id"
GROUP_KEY = "group_id"  # the group the user is looking at (they can be in several)
NEXT_GROUP_KEY = "next_group_id"  # a group to switch to on the next run (after create or join)


def _remember(user: User) -> None:
    """Keep only the user's id in the session; everything else is loaded fresh."""
    st.session_state[USER_KEY] = user.id


def log_out() -> None:
    """Forget the logged-in user and show the log-in page."""
    st.session_state.pop(USER_KEY, None)
    st.session_state.pop(GROUP_KEY, None)
    st.session_state.pop(NEXT_GROUP_KEY, None)
    st.rerun()


def _login_form() -> None:
    """Draw the log-in form and log the user in."""
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            attempt(lambda: _remember(auth.log_in(username, password)))
            st.rerun()


def _signup_form() -> None:
    """Draw the sign-up form; a new user is logged in straight away."""
    with st.form("signup"):
        username = st.text_input(
            "Choose a username",
            help=f"{auth.MIN_USERNAME_LENGTH} to {auth.MAX_USERNAME_LENGTH} characters.",
        )
        password = st.text_input(
            "Choose a password", type="password",
            help=f"At least {auth.MIN_PASSWORD_LENGTH} characters.",
        )
        if st.form_submit_button("Create account", type="primary"):
            attempt(lambda: _remember(auth.sign_up(username, password)))
            st.rerun()


def login_page() -> None:
    """Draw the page for visitors who are not logged in."""
    _, middle, _ = st.columns([1, 1.6, 1])
    with middle:
        style.show(
            f'<div style="margin:56px 0 6px">{style.logo(40)}</div>'
            '<div class="ci-h1" style="font-size:2.4rem">Bet on your friends.</div>'
            '<div class="ci-muted" style="margin-bottom:18px">Virtual coins, no bookmaker: '
            "the group sets the odds.</div>"
        )
        show_flash()
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
        with login_tab:
            _login_form()
        with signup_tab:
            _signup_form()


def _switch_to(group) -> None:
    """Show this group from the next run on (after creating or joining it).

    GROUP_KEY belongs to the group dropdown, which is already drawn in this
    run, so the switch is noted here and made by chosen_group() next run.
    """
    st.session_state[NEXT_GROUP_KEY] = group.id


def chosen_group():
    """Return the group to show, first applying a switch noted by _switch_to.

    Call before the group dropdown is drawn (app.load_page does).
    """
    if NEXT_GROUP_KEY in st.session_state:
        st.session_state[GROUP_KEY] = st.session_state.pop(NEXT_GROUP_KEY)
    return st.session_state.get(GROUP_KEY)


def create_group_form(user: User) -> None:
    """Draw the form that creates a group with the user as its admin, then shows it."""
    with st.form("create_group"):
        st.subheader("Create a group")
        name = st.text_input("Group name", max_chars=auth.MAX_GROUP_NAME_LENGTH)
        st.caption("Everyone starts at 0. Once your friends have joined, top up "
                   "everyone at once in the Group tab.")
        if st.form_submit_button("Create group", type="primary"):
            attempt(
                lambda: _switch_to(auth.create_group(user.id, name)),
                "Group created. Share the invite code, then top up everyone.",
            )
            st.rerun()


def join_group_form(user: User) -> None:
    """Draw the form that joins a group by invite code, then shows it."""
    with st.form("join_group"):
        st.subheader("Join a group")
        code = st.text_input("Invite code", max_chars=20)
        if st.form_submit_button("Join group", type="primary"):
            attempt(lambda: _switch_to(auth.join_group(user.id, code)), "You joined the group.")
            st.rerun()


def group_page(user: User) -> None:
    """Draw the page for a logged-in user who has no group yet."""
    style.show(
        f'<div style="margin:24px 0 4px">{style.logo()}</div>'
        + style.heading(f"Welcome, {user.username}",
                        "Create a group and invite friends, or join one with an invite code")
    )
    st.write("")
    show_flash()
    left, right = st.columns(2, gap="large")
    with left:
        create_group_form(user)
    with right:
        join_group_form(user)
    if st.button("Log out"):
        log_out()

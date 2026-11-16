"""Screens before the feed: log in, sign up, and create or join a group."""

from decimal import Decimal

import streamlit as st

from src import auth
from src.models import User
from src.ui.actions import attempt, show_flash

USER_KEY = "user_id"


def _remember(user: User) -> None:
    """Keep only the user's id in the session; everything else is loaded fresh."""
    st.session_state[USER_KEY] = user.id


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
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title("Called it!")
        st.caption("Bet virtual coins with your friends. The group sets the odds.")
        show_flash()
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
        with login_tab:
            _login_form()
        with signup_tab:
            _signup_form()


def _create_group_form(user: User) -> None:
    """Draw the form that creates a group with the user as its admin."""
    with st.form("create_group"):
        st.subheader("Create a group")
        name = st.text_input("Group name", max_chars=auth.MAX_GROUP_NAME_LENGTH)
        start = st.number_input(
            "Starting balance for every member",
            min_value=1, value=int(auth.DEFAULT_START_BALANCE), step=100,
        )
        if st.form_submit_button("Create group", type="primary"):
            attempt(
                lambda: auth.create_group(user.id, name, Decimal(int(start))),
                "Group created. Share the invite code with your friends.",
            )
            st.rerun()


def _join_group_form(user: User) -> None:
    """Draw the form that joins a group by invite code."""
    with st.form("join_group"):
        st.subheader("Join a group")
        code = st.text_input("Invite code", max_chars=20)
        if st.form_submit_button("Join group", type="primary"):
            attempt(lambda: auth.join_group(user.id, code), "You joined the group.")
            st.rerun()


def group_page(user: User) -> None:
    """Draw the page for a logged-in user who has no group yet."""
    st.title(f"Welcome, {user.username}")
    st.caption("Create a group and invite friends, or join one with an invite code.")
    show_flash()
    left, right = st.columns(2, gap="large")
    with left:
        _create_group_form(user)
    with right:
        _join_group_form(user)
    if st.button("Log out"):
        st.session_state.pop(USER_KEY, None)
        st.rerun()

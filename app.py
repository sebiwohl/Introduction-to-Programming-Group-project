"""Called it! Streamlit entry point: start-up checks and page routing.

Run locally:  streamlit run app.py
Only the user's id is kept in the session. Every run loads the data once through
service.page_data(), so every user sees the saved state at one read per click.
"""

import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from src import service, stats, storage
from src.betting import BettingError
from src.service import PageData
from src.ui import account, events, group, leaderboard, my_bets, nav, profile, style
from src.ui.actions import show_flash, timed


def use_secret_database_url() -> None:
    """Copy DATABASE_URL from Streamlit's secrets into the environment, if set.

    storage.py must not import Streamlit, so it reads the environment only. With
    no secrets file (local development) nothing happens and the JSON file is used.
    If DATA_FILE is set, the developer chose a local file, so the secret is ignored.
    """
    if os.environ.get(storage.DATABASE_URL_ENV) or os.environ.get("DATA_FILE"):
        return
    try:
        value = st.secrets.get(storage.DATABASE_URL_ENV)
    except Exception:  # no secrets file: the error type differs between versions
        value = None
    if value:
        os.environ[storage.DATABASE_URL_ENV] = value


@st.cache_resource
def check_storage_once() -> Optional[str]:
    """Load the data once per app start, restoring a backup if it is damaged.

    Returns a warning to show if a backup was restored. Raises StorageError if
    the data cannot be loaded at all (that failure is not cached).
    """
    _, warning = storage.load_with_recovery()
    return warning


def load_page(now: datetime) -> Optional[PageData]:
    """Return everything for the logged-in user from one load, or None.

    A stale user id (for example, the account no longer exists) is forgotten.
    """
    user_id = st.session_state.get(account.USER_KEY)
    if not user_id:
        return None
    try:
        with timed("load page data"):
            return service.page_data(user_id, now,
                                     group_id=account.chosen_group())
    except BettingError:
        st.session_state.pop(account.USER_KEY, None)
        return None


def show_app(page: PageData, now: datetime) -> None:
    """Draw the top bar and the page the user picked."""
    if page.group is None:
        account.group_page(page.user)
        return
    style.remember_pictures({r.username: r.avatar for r in page.leaderboard})
    member = stats.member_stats(page.events)
    me = member.get(page.user.username, stats.MemberStats())
    choice = nav.top_bar(page, me, lambda: events.new_event_dialog(page.user, now))
    show_flash()
    with st.container(key=nav.page_key(choice)):  # fades in when the page changes
        if choice == nav.MY_BETS:
            my_bets.my_bets_page(page, me)
        elif choice == nav.LEADERBOARD:
            leaderboard.leaderboard_page(page, member)
        elif choice == nav.PROFILE:
            profile.profile_page(page, me)
        elif choice == nav.GROUP:
            group.group_page(page)
        else:
            events.events_page(page, me, now)


def main() -> None:
    """Set up the page, check the storage and show the right screen."""
    st.set_page_config(page_title="Called it!", layout="wide")
    style.inject_css()
    use_secret_database_url()
    try:
        warning = check_storage_once()
        if warning:
            st.warning(warning)
        now = datetime.now(timezone.utc)
        page = load_page(now)
        if page is None:
            account.login_page()
        else:
            show_app(page, now)
    except storage.StorageError as error:
        st.error(f"The app's data could not be loaded or saved: {error}")


with timed("whole page run"):
    main()

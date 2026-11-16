"""Called it! Streamlit entry point: start-up checks and page routing.

Run locally:  streamlit run app.py
Only the user's id is kept in the session; all data is loaded fresh through
service.py on every run, so every user sees the saved state.
"""

import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from src import service, storage
from src.betting import BettingError
from src.models import User
from src.ui import account, events, pages, style
from src.ui.actions import show_flash


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


def current_user() -> Optional[User]:
    """Return the logged-in user loaded fresh, or None (forgetting a stale id)."""
    user_id = st.session_state.get(account.USER_KEY)
    if not user_id:
        return None
    try:
        return service.get_user(user_id)
    except BettingError:
        st.session_state.pop(account.USER_KEY, None)
        return None


def show_app(user: User) -> None:
    """Draw the sidebar and the page the user picked."""
    group = service.get_group(user.id)
    if group is None:
        account.group_page(user)
        return
    page = pages.sidebar(user, group)
    show_flash()
    if page == "Balances":
        pages.balances_page(user)
    elif page == "Profile":
        pages.profile_page(user, group)
    else:
        events.events_page(user, datetime.now(timezone.utc))


def main() -> None:
    """Set up the page, check the storage and show the right screen."""
    st.set_page_config(page_title="Called it!", layout="wide")
    style.inject_css()
    use_secret_database_url()
    try:
        warning = check_storage_once()
        if warning:
            st.warning(warning)
        user = current_user()
        if user is None:
            account.login_page()
        else:
            show_app(user)
    except storage.StorageError as error:
        st.error(f"The app's data could not be loaded or saved: {error}")


main()

"""Run an action for the user and show the result as a message on the next run."""

from typing import Callable, Optional

import streamlit as st

from src.betting import BettingError
from src.storage import StorageError

FLASH_KEY = "flash"


def attempt(action: Callable[[], object], success: Optional[str] = None) -> bool:
    """Run action(); remember a success or error message; return True if it worked.

    Rule violations (BettingError) and storage problems (StorageError) are shown
    to the user instead of crashing the page.
    """
    try:
        action()
    except (BettingError, StorageError) as error:
        st.session_state[FLASH_KEY] = ("error", str(error))
        return False
    if success:
        st.session_state[FLASH_KEY] = ("success", success)
    return True


def show_flash() -> None:
    """Show the remembered message once, then forget it."""
    kind, text = st.session_state.pop(FLASH_KEY, (None, None))
    if kind == "error":
        st.error(text)
    elif kind == "success":
        st.success(text)

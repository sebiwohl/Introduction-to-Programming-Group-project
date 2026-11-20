"""Run an action for the user and show the result as a message on the next run."""

import contextlib
import html
import logging
import time
from typing import Callable, Iterator, Optional

import streamlit as st

from src.betting import BettingError
from src.storage import StorageError

FLASH_KEY = "flash"

# Timing lines go to the server log (Streamlit Cloud: Manage app > Logs), so we
# can see whether a slow click is spent loading, saving or drawing the page.
_TIMING_LOG = logging.getLogger("called_it.timing")
if not _TIMING_LOG.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [timing] %(message)s"))
    _TIMING_LOG.addHandler(_handler)
    _TIMING_LOG.setLevel(logging.INFO)
    _TIMING_LOG.propagate = False


@contextlib.contextmanager
def timed(label: str) -> Iterator[None]:
    """Log how long the block took, like '[timing] load page: 0.123 s'."""
    start = time.perf_counter()
    try:
        yield
    finally:
        _TIMING_LOG.info("%s: %.3f s", label, time.perf_counter() - start)


def attempt(action: Callable[[], object], success: Optional[str] = None) -> bool:
    """Run action(); remember a success or error message; return True if it worked.

    Rule violations (BettingError) and storage problems (StorageError) are shown
    to the user instead of crashing the page. The time it took is logged.
    """
    try:
        with timed("action"):
            action()
    except (BettingError, StorageError) as error:
        st.session_state[FLASH_KEY] = ("error", str(error))
        return False
    if success:
        st.session_state[FLASH_KEY] = ("success", success)
    return True


FLASH_ICONS = {"success": "✓", "error": "!"}


def flash_markup(kind: str, text: str, number: int) -> str:
    """Return the message as a floating card (see .ci-flash in style.py).

    number makes each message's HTML different, so a repeated message is drawn
    (and slides in) again.
    """
    return (f'<div class="ci-flash ci-flash-{kind}" data-n="{number}" role="status">'
            f'<span class="ci-flash-icon">{FLASH_ICONS[kind]}</span>'
            f'<span class="ci-flash-text">{html.escape(text)}</span></div>')


def show_flash() -> None:
    """Show the remembered message once, then forget it.

    It floats at the top of the screen (not the top of the page), so it is seen
    wherever the button was, then fades out by itself.
    """
    kind, text = st.session_state.pop(FLASH_KEY, (None, None))
    if kind not in FLASH_ICONS:
        return
    number = st.session_state.get("flash_count", 0) + 1
    st.session_state["flash_count"] = number
    st.markdown(flash_markup(kind, text, number), unsafe_allow_html=True)

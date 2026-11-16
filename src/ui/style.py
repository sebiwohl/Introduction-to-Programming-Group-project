"""Look and feel: the one CSS block and the small HTML pieces the screens share.

To restyle the app, change `.streamlit/config.toml` (colours, font) and the CSS
below. All text that comes from users is escaped before it is put into HTML.
"""

import html
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import streamlit as st

APP_TIMEZONE = ZoneInfo("Europe/Lisbon")

CSS = """
<style>
.ci-title {font-weight: 600; font-size: 1.05rem;}
.ci-row {display: flex; justify-content: space-between; align-items: center; gap: 8px;}
.ci-muted {color: #5F5E5A; font-size: 0.85rem;}
.ci-badge {border-radius: 6px; padding: 2px 10px; font-size: 0.8rem; white-space: nowrap;}
.ci-open {background: #EAF3DE; color: #27500A;}
.ci-waiting {background: #FAEEDA; color: #633806;}
.ci-resolved {background: #E6F1FB; color: #0C447C;}
.ci-pct {display: flex; justify-content: space-between; font-size: 0.8rem; margin-top: 8px;}
.ci-yes {color: #3B6D11;}
.ci-no {color: #A32D2D;}
.ci-bar {display: flex; height: 8px; border-radius: 4px; overflow: hidden;
         margin: 4px 0 2px; background: #D3D1C7;}
.ci-bar-yes {background: #639922;}
.ci-bar-no {background: #E24B4A;}
.ci-board {display: flex; justify-content: space-between; background: #FFFFFF;
           border-radius: 8px; padding: 6px 10px; margin-bottom: 6px;}
.ci-board-me {outline: 1.5px solid #B4B2A9;}
.ci-code {font-family: monospace; font-size: 1.05rem; letter-spacing: 1px;}
[class*="st-key-card_"], [data-testid="stForm"],
[data-testid="stExpander"] details {background: #FFFFFF;}
[class*="st-key-yes_"] button {background: #EAF3DE; color: #27500A; border-color: #C0DD97;}
[class*="st-key-no_"] button {background: #FCEBEB; color: #791F1F; border-color: #F7C1C1;}
</style>
"""


def inject_css() -> None:
    """Add the app's CSS to the page (call once per run)."""
    st.markdown(CSS, unsafe_allow_html=True)


def money(amount: Decimal) -> str:
    """Format coins with thousands separators and 2 decimals."""
    return f"{amount:,.2f}"


def when(moment: datetime) -> str:
    """Format a time in the app's timezone, like 'Fri 20 Nov, 19:00'."""
    return moment.astimezone(APP_TIMEZONE).strftime("%a %d %b, %H:%M")


def badge(text: str, kind: str) -> str:
    """Return a status badge; kind is 'open', 'waiting' or 'resolved'."""
    return f'<span class="ci-badge ci-{kind}">{html.escape(text)}</span>'


def card_header(title: str, badge_html: str, description: str, meta: str) -> str:
    """Return the top of an event card: title, badge, description and a meta line."""
    parts = [
        f'<div class="ci-row"><span class="ci-title">{html.escape(title)}</span>'
        f"{badge_html}</div>"
    ]
    if description:
        parts.append(f"<div>{html.escape(description)}</div>")
    parts.append(f'<div class="ci-muted">{html.escape(meta)}</div>')
    return "".join(parts)


def odds_bar(stake_yes: Decimal, stake_no: Decimal) -> str:
    """Return the green/red bar showing how the pool splits between yes and no."""
    pool = stake_yes + stake_no
    if pool == 0:
        return '<div class="ci-pct ci-muted">No bets yet</div><div class="ci-bar"></div>'
    yes = round(stake_yes / pool * 100)
    return (
        f'<div class="ci-pct"><span class="ci-yes">Yes {yes}%</span>'
        f'<span class="ci-no">No {100 - yes}%</span></div>'
        f'<div class="ci-bar"><div class="ci-bar-yes" style="width:{yes}%"></div>'
        f'<div class="ci-bar-no" style="width:{100 - yes}%"></div></div>'
    )


def board_row(rank: int, username: str, balance: Decimal, is_me: bool) -> str:
    """Return one leaderboard line; the viewer's own line is outlined."""
    extra = " ci-board-me" if is_me else ""
    return (
        f'<div class="ci-board{extra}"><span>{rank} · {html.escape(username)}</span>'
        f"<strong>{money(balance)}</strong></div>"
    )


def muted(text: str) -> str:
    """Return a small grey line of text."""
    return f'<div class="ci-muted">{html.escape(text)}</div>'

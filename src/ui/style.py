"""Look and feel: the one CSS block, the logo and the small HTML pieces the screens share.

To restyle the app, change `.streamlit/config.toml` (colours, font) and the CSS
below. All text that comes from users is escaped before it is put into HTML.
"""

import base64
import html
import re
from datetime import datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import streamlit as st

APP_TIMEZONE = ZoneInfo("Europe/Lisbon")
MINUS = "−"

# Pastel avatar backgrounds; a member always gets the same one (see avatar()).
AVATAR_COLOURS = ("#E9E7F7", "#F6E6DD", "#E3F0E6", "#FBF1DA", "#EDE3F3", "#E2EEF7")

CSS = """
<style>
:root {
  --ci-ink: #0A0A0A; --ci-text: #111111; --ci-muted: #6B6B72; --ci-line: #E6E6EB;
  --ci-soft: #F4F4F6; --ci-yes: #3B3BE6; --ci-yes-soft: #EEEFFE; --ci-no: #C8460F;
  --ci-no-soft: #FFF4EC; --ci-no-line: #EBA987;
}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
[data-testid="stMainBlockContainer"] {max-width: 1120px; padding: 0 24px 72px;}
[data-testid="stMarkdownContainer"] p {margin-bottom: 0;}
.stMarkdown [data-testid="stMarkdownContainer"] {margin-bottom: 0;}
button {border-radius: 999px !important; font-weight: 600 !important;}

/* top bar */
.st-key-topbar {position: sticky; top: 0; z-index: 100; background: #FFFFFF;
  border-bottom: 1px solid var(--ci-line); padding: 10px 0 8px; margin-bottom: 18px;}
.ci-logo {display: inline-flex; align-items: center; gap: 8px; text-decoration: none;
  color: var(--ci-ink); white-space: nowrap;}
.ci-logo-word {font-weight: 900; letter-spacing: -0.03em; line-height: 1;}
.ci-topstats {display: flex; justify-content: flex-end; align-items: center; gap: 14px;
  font-weight: 700; white-space: nowrap;}
.ci-streak {color: var(--ci-no); display: inline-flex; align-items: center; gap: 3px;}
.ci-coins {display: inline-flex; align-items: center; gap: 5px;}

/* radio buttons drawn as tabs (key "nav" or "tabs_...") or pills (key "pills_...") */
.st-key-nav [role="radiogroup"], [class*="st-key-tabs_"] [role="radiogroup"],
[class*="st-key-pills_"] [role="radiogroup"] {flex-wrap: wrap; gap: 4px;}
.st-key-nav [data-testid="stRadioOption"], [class*="st-key-tabs_"] [data-testid="stRadioOption"],
[class*="st-key-pills_"] [data-testid="stRadioOption"] {margin: 0; cursor: pointer;}
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-tabs_"] [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-pills_"] [data-testid="stRadioOption"] > div > div:first-child {display: none;}
.st-key-nav [data-testid="stRadioOption"], [class*="st-key-tabs_"] [data-testid="stRadioOption"] {
  padding: 6px 10px; border-bottom: 2px solid transparent;}
.st-key-nav [data-testid="stRadioOption"] p, [class*="st-key-tabs_"] [data-testid="stRadioOption"] p {
  color: var(--ci-muted); font-weight: 500;}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"],
[class*="st-key-tabs_"] [data-testid="stRadioOption"][data-selected="true"] {
  border-bottom-color: var(--ci-ink);}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p,
[class*="st-key-tabs_"] [data-testid="stRadioOption"][data-selected="true"] p {
  color: var(--ci-ink); font-weight: 700;}
[class*="st-key-tabs_"] [data-testid="stRadioOption"] p {font-size: 0.88rem;}
[class*="st-key-tabs_"] [role="radiogroup"] {border-bottom: 1px solid var(--ci-line); gap: 0;}
[class*="st-key-pills_"] [role="radiogroup"] {justify-content: flex-end; gap: 8px;}
[class*="st-key-pills_"] [data-testid="stRadioOption"] {border: 1px solid var(--ci-ink);
  border-radius: 999px; padding: 5px 14px;}
[class*="st-key-pills_"] [data-testid="stRadioOption"] p {font-size: 0.85rem; font-weight: 600;}
[class*="st-key-pills_"] [data-testid="stRadioOption"][data-selected="true"] {background: var(--ci-ink);}
[class*="st-key-pills_"] [data-testid="stRadioOption"][data-selected="true"] p {color: #FFFFFF;}

/* headings and small labels */
.ci-h1 {font-size: 3.1rem; font-weight: 900; letter-spacing: -0.045em; line-height: 1.05;
  margin: 6px 0 4px; color: var(--ci-ink);}
.ci-kicker {font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ci-muted);}
.ci-label {font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ci-text);}
.ci-muted {color: var(--ci-muted); font-size: 0.85rem;}
.ci-yes {color: var(--ci-yes);}
.ci-no {color: var(--ci-no);}
.ci-row {display: flex; justify-content: space-between; align-items: center; gap: 10px;}

/* pills and badges */
.ci-pill {display: inline-block; border-radius: 4px; padding: 2px 6px; font-size: 0.66rem;
  font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
  border: 1px solid transparent; line-height: 1.3;}
.ci-pill-open, .ci-pill-resolved {background: var(--ci-ink); color: #FFFFFF;}
.ci-pill-paused, .ci-pill-lost {background: var(--ci-soft); color: var(--ci-muted);
  border-color: var(--ci-line);}
.ci-pill-waiting {background: var(--ci-no); color: #FFFFFF;}
.ci-pill-big {border-color: var(--ci-no); color: var(--ci-no);}
.ci-pill-won {background: var(--ci-yes); color: #FFFFFF;}
.ci-pill-refunded {border-color: var(--ci-line); color: var(--ci-text);}
.ci-pill-yes {background: var(--ci-yes-soft); color: var(--ci-yes);}
.ci-pill-no {background: var(--ci-no-soft); color: var(--ci-no);}
.ci-admin {display: inline-flex; align-items: center; gap: 3px; background: var(--ci-ink);
  color: #FFFFFF; border-radius: 4px; padding: 1px 6px; font-size: 0.6rem; font-weight: 800;
  letter-spacing: 0.06em; vertical-align: middle; margin-left: 6px;}
.ci-avatar {display: inline-flex; align-items: center; justify-content: center; flex: none;
  width: 24px; height: 24px; border-radius: 50%; font-size: 0.72rem; font-weight: 700;
  color: var(--ci-text);}
.ci-avatar-me {background: var(--ci-ink) !important; color: #FFFFFF;}
.ci-who {display: inline-flex; align-items: center; gap: 10px; min-width: 0; white-space: nowrap;}

/* event items in the feed */
[class*="st-key-ev_"] {border-bottom: 1px solid var(--ci-line); padding: 16px 0 18px; gap: 0.6rem;}
[class*="st-key-wait_"] {border: 1.5px dashed var(--ci-no-line); background: #FFF9F5;
  border-radius: 16px; padding: 16px 18px 18px; margin: 14px 0; gap: 0.6rem;}
.ci-ev {display: flex; justify-content: space-between; gap: 16px; align-items: flex-start;}
.ci-ev-main {min-width: 0;}
.ci-ev-tags {display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  font-size: 0.75rem; color: var(--ci-muted);}
.ci-ev-title {font-size: 1.3rem; font-weight: 800; letter-spacing: -0.02em; margin: 6px 0 2px;
  color: var(--ci-ink); line-height: 1.25;}
.ci-ev-desc {font-size: 0.9rem; margin: 2px 0;}
.ci-ev-sub {font-size: 0.78rem; color: var(--ci-muted);}
.ci-odds {display: flex; gap: 8px; flex: none;}
.ci-odd {width: 66px; border-radius: 10px; padding: 6px 0 5px; text-align: center;
  background: var(--ci-soft); border: 1px solid var(--ci-line);}
.ci-odd b {display: block; font-size: 1.3rem; font-weight: 800; line-height: 1.2;}
.ci-odd span {font-size: 0.6rem; font-weight: 800; letter-spacing: 0.08em; color: var(--ci-muted);}
.ci-odd-yes b {color: var(--ci-yes);}
.ci-odd-no b {color: var(--ci-no);}
.ci-odd-final {background: #FFFFFF;}
.ci-odd-final b {color: var(--ci-text);}
.ci-odd-win {outline: 2px solid var(--ci-ink);}
.ci-split {display: flex; justify-content: space-between; gap: 8px; font-size: 0.7rem;
  font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; margin-top: 10px;}
.ci-split-mid {color: var(--ci-muted);}
.ci-bar {display: flex; gap: 4px; height: 6px; margin-top: 4px;}
.ci-bar div {border-radius: 999px; min-width: 6px;}
.ci-bar-yes {background: var(--ci-yes);}
.ci-bar-no {background: var(--ci-no);}
.ci-bar-empty {background: var(--ci-line); flex: 1;}
.ci-bar-grey .ci-bar-yes {background: #8E8E95;}
.ci-bar-grey .ci-bar-no {background: #CFCFD5;}

/* bet form, result buttons and the bets list */
[class*="st-key-yes_"] button {background: var(--ci-yes-soft); color: var(--ci-yes);
  border-color: #C9CBF7;}
[class*="st-key-no_"] button {background: var(--ci-no-soft); color: var(--ci-no);
  border-color: #F3CDB8;}
[class*="st-key-yes_"] button:hover {border-color: var(--ci-yes); color: var(--ci-yes);}
[class*="st-key-no_"] button:hover {border-color: var(--ci-no); color: var(--ci-no);}
[class*="st-key-ev_"] [data-testid="stForm"], [class*="st-key-wait_"] [data-testid="stForm"] {
  padding: 0; border: none;}
[class*="st-key-bets_"] details {border: none !important; background: transparent;}
[class*="st-key-bets_"] summary {display: inline-flex; width: auto; padding: 4px 14px;
  border: 1px solid var(--ci-line); border-radius: 999px; font-size: 0.82rem; font-weight: 600;}
[class*="st-key-bets_"] [data-testid="stExpanderDetails"] {padding: 10px 0 0;}
.ci-sides {display: grid; grid-template-columns: 1fr 1fr; gap: 10px;}
.ci-side {background: var(--ci-soft); border-radius: 12px; padding: 12px 14px;}
.ci-side-row {display: flex; justify-content: space-between; align-items: center;
  padding: 4px 0; font-size: 0.88rem;}

/* names events: one row per option instead of the YES / NO boxes and bar */
.ci-opts {display: flex; flex-direction: column; gap: 4px;}
.ci-opt {display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(60px, 2fr) 40px 112px;
  grid-template-areas: "name bar share odds"; align-items: center; gap: 12px;
  background: var(--ci-soft); border: 1px solid var(--ci-line); border-radius: 10px; padding: 7px 12px;}
.ci-opt-name {grid-area: name; min-width: 0; font-size: 0.92rem; font-weight: 700; color: var(--ci-ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}
.ci-opt-sub {display: block; font-size: 0.7rem; font-weight: 500; color: var(--ci-muted);
  overflow: hidden; text-overflow: ellipsis;}
.ci-opt-bar {grid-area: bar; height: 6px; border-radius: 999px; background: var(--ci-line); overflow: hidden;}
.ci-opt-bar div {height: 100%; border-radius: 999px; background: var(--ci-yes);}
.ci-opt-share {grid-area: share; font-size: 0.78rem; font-weight: 700; text-align: right;}
.ci-opt-odds {grid-area: odds; font-size: 1.05rem; font-weight: 800; text-align: right; white-space: nowrap;
  color: var(--ci-yes);}
.ci-opt-odds small {font-size: 0.6rem; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--ci-muted);}
.ci-opts-final .ci-opt {background: #FFFFFF;}
.ci-opts-final .ci-opt-odds {color: var(--ci-text);}
.ci-opts-final .ci-opt-bar div, .ci-opts-grey .ci-opt-bar div {background: #8E8E95;}
.ci-opts .ci-opt-win {outline: 2px solid var(--ci-ink); border-color: transparent;}
.ci-opts .ci-opt-win .ci-opt-bar div {background: var(--ci-yes);}
.ci-opt-sub .ci-pill {margin-right: 4px;}
.ci-ev-tags .ci-pill-won {max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}
.ci-help {display: inline-block; width: 14px; height: 14px; line-height: 14px; border-radius: 50%;
  background: var(--ci-line); color: var(--ci-text); font-size: 0.6rem; font-weight: 800;
  text-align: center; margin-left: 4px; cursor: help;}

/* cards */
.ci-card, [class*="st-key-card_"] {border: 1px solid var(--ci-line); border-radius: 16px;
  padding: 16px 18px; background: #FFFFFF;}
[class*="st-key-card_"] .ci-card {border: none; padding: 0;}
.ci-card + .ci-card {margin-top: 14px;}
.ci-card-dark {background: var(--ci-ink); color: #FFFFFF; border-color: var(--ci-ink);}
.ci-card-dark .ci-kicker {color: #B4B4BC;}
.ci-big {font-size: 2.6rem; font-weight: 900; letter-spacing: -0.04em; line-height: 1;}
.ci-board-row {display: flex; align-items: center; gap: 10px; padding: 6px 8px;
  border-radius: 10px; font-size: 0.9rem;}
.ci-board-row b {margin-left: auto;}
.ci-board-me {background: var(--ci-soft);}
.ci-rank {width: 16px; font-weight: 700;}
.ci-progress {height: 4px; border-radius: 999px; background: var(--ci-line); margin-top: 8px;}
.ci-progress div {height: 4px; border-radius: 999px; background: var(--ci-yes);}

/* banner, stat tiles and tables */
.ci-banner {display: flex; justify-content: space-between; align-items: center; gap: 16px;
  background: var(--ci-ink); color: #FFFFFF; border-radius: 14px; padding: 14px 18px;
  margin: 12px 0 4px;}
.ci-banner .ci-kicker {color: #9C9CF5;}
.ci-banner-title {font-weight: 800; font-size: 1.05rem;}
.ci-banner-sub {font-size: 0.8rem; color: #C9C9CF;}
.ci-banner-win {font-size: 2rem; font-weight: 900; white-space: nowrap;}
.ci-banner-win small {font-size: 0.65rem; font-weight: 700; letter-spacing: 0.06em;}
.ci-tiles {display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 18px 0 26px;}
.ci-tile {border-radius: 16px; padding: 16px 18px; background: var(--ci-soft);
  border: 1px solid var(--ci-line);}
.ci-tile .ci-big {margin: 8px 0 6px;}
.ci-tile-dark {background: var(--ci-ink); color: #FFFFFF; border-color: var(--ci-ink);}
.ci-tile-dark .ci-kicker, .ci-tile-dark .ci-muted {color: #C9C9CF;}
.ci-tile-warm {background: var(--ci-no-soft); border-color: #F6D9C6;}
.ci-tile-warm .ci-kicker {color: var(--ci-no);}
.ci-table-wrap {border: 1px solid var(--ci-line); border-radius: 16px; overflow-x: auto;}
.ci-table {width: 100%; border-collapse: collapse; font-size: 0.88rem;}
.ci-table th {text-align: left; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.08em;
  color: var(--ci-muted); padding: 12px 16px; border-bottom: 1px solid var(--ci-line);
  white-space: nowrap; text-transform: uppercase;}
.ci-table td {padding: 11px 16px; border-bottom: 1px solid var(--ci-line); vertical-align: middle;}
.ci-table tr:last-child td {border-bottom: none;}
.ci-table .ci-num {text-align: right; white-space: nowrap;}
.ci-table tr.ci-me td {background: #F0F0FE;}
.ci-table tr.ci-warm td {background: #FFF6EF;}
.ci-t-title {font-weight: 700;}
.ci-gain {color: var(--ci-yes); font-weight: 800;}
.ci-loss {color: var(--ci-text); font-weight: 800;}
.ci-podium {display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 18px 0 26px;}
.ci-podium-card {border-radius: 16px; padding: 16px 18px; background: var(--ci-soft);
  border: 1px solid var(--ci-line); min-height: 140px;}
.ci-podium-first {background: var(--ci-ink); color: #FFFFFF; border-color: var(--ci-ink);}
.ci-podium-first .ci-muted {color: #C9C9CF;}
.ci-podium-me {border: 1.5px solid var(--ci-yes); background: #FFFFFF;}
.ci-podium-me .ci-muted {color: var(--ci-yes);}
.ci-podium .ci-avatar {width: 36px; height: 36px; font-size: 0.95rem;}
.ci-podium-rank {font-size: 3rem; font-weight: 900; line-height: 1;}
.ci-podium-name {font-size: 1.1rem; font-weight: 800; margin-top: 18px;}
.ci-h2h {display: grid; grid-template-columns: 1fr auto 1fr; row-gap: 10px; align-items: center;
  font-size: 0.95rem;}
.ci-h2h .ci-kicker {text-align: center;}
.ci-h2h .ci-right {text-align: right;}
.ci-better {font-weight: 800; color: var(--ci-yes);}
.ci-note {background: var(--ci-soft); border-radius: 10px; padding: 10px 12px; font-weight: 700;
  font-size: 0.88rem; margin-top: 14px;}

/* group page */
.st-key-invite {background: var(--ci-ink); color: #FFFFFF; border-radius: 16px;
  padding: 16px 18px; gap: 0.4rem;}
.st-key-invite .ci-kicker {color: #B4B4BC;}
.st-key-invite [data-testid="stCode"] pre {background: transparent; padding: 0;}
.st-key-invite [data-testid="stCode"] code {color: #FFFFFF; font-family: "Inter", sans-serif !important;
  font-size: 2.2rem; font-weight: 900; letter-spacing: 0.04em;}
.st-key-invite [data-testid="stCode"] button {filter: invert(1);}
.ci-member {display: flex; align-items: center; gap: 12px; padding: 9px 0;
  border-bottom: 1px solid var(--ci-line);}
.ci-member:last-child {border-bottom: none;}
.ci-member-money {margin-left: auto; text-align: right;}
.ci-rules p {font-size: 0.86rem; margin: 0 0 8px;}

@media (max-width: 760px) {
  .ci-h1 {font-size: 2.2rem;}
  .ci-tiles, .ci-podium {grid-template-columns: 1fr 1fr;}
  .ci-sides {grid-template-columns: 1fr;}
  .ci-ev {flex-direction: column;}
  .ci-opt {grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "name odds" "bar share";
    row-gap: 6px;}
  .st-key-topbar {position: static;}
}
</style>
"""

def svg_image(svg: str, width: int, height: int) -> str:
    """Return an SVG drawing as an <img> tag with a data URI.

    Inline <svg> elements do not survive Streamlit's HTML cleaning, images do,
    so every icon and the logo are embedded this way. Nothing is loaded from another host.
    """
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (f'<img src="data:image/svg+xml;base64,{data}" width="{width}" height="{height}"'
            ' alt="" style="display:inline-block;vertical-align:middle">')


FLAME = svg_image(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 14"><path d="M6 1c.4 2.2 1.9 3.4'
    ' 3.1 4.8C10.4 7.3 11 8.4 11 9.6 11 12 8.8 13.4 6 13.4S1 12 1 9.6c0-1.6.8-3 1.9-4.1.1 1.2.7'
    ' 2.1 1.6 2.4C4 6.1 4.6 3.2 6 1z" fill="none" stroke="#C8460F" stroke-width="1.5"'
    ' stroke-linejoin="round"/></svg>', 12, 14,
)
COIN = svg_image(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6"'
    ' fill="none" stroke="#0A0A0A" stroke-width="1.8"/><circle cx="8" cy="8" r="3" fill="none"'
    ' stroke="#0A0A0A" stroke-width="1.8"/></svg>', 15, 15,
)
CROWN = svg_image(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 8"><path d="M1 7h8L9.4 1.6 7 3.8'
    ' 5 .8 3 3.8.6 1.6z" fill="none" stroke="#FFFFFF" stroke-width="1.2"'
    ' stroke-linejoin="round"/></svg>', 9, 8,
)


def inject_css() -> None:
    """Add the app's CSS to the page (call once per run)."""
    st.markdown(CSS, unsafe_allow_html=True)


def show(markup: str, where=st) -> None:
    """Draw a piece of HTML built by these helpers, optionally inside a column.

    The HTML is one line without blank lines, so Markdown leaves it alone.
    (st.html would be simpler, but it removes the logo's images.)
    """
    if markup:
        where.markdown(markup, unsafe_allow_html=True)


def esc(text: str) -> str:
    """Escape text from users so it is shown as text, never as HTML."""
    return html.escape(text or "")


MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>$:])")


def plain(text: str) -> str:
    """Escape Markdown so user text shows literally in widget labels (radio, pills, checkboxes).

    Streamlit renders labels as Markdown, so a name like '[x](link)' would
    otherwise become a link. HTML is never rendered there.
    """
    return MARKDOWN_SPECIAL.sub(r"\\\1", text or "")


def logo(height: int = 26) -> str:
    """Return the "CALLED IT!" logo: a speech bubble with a tick, then the wordmark.

    The exclamation mark is drawn in the two betting colours: an orange-red
    bar for NO and a blue dot for YES.
    """
    word = round(height * 0.72)
    bubble = svg_image(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<path d="M7 3h18a5 5 0 0 1 5 5v11a5 5 0 0 1-5 5H15l-6.5 5.2a.8.8 0 0 1-1.3-.6V24H7'
        'a5 5 0 0 1-5-5V8a5 5 0 0 1 5-5z" fill="#0A0A0A"/>'
        '<path d="M10.2 13.6l4 4 7.8-7.8" fill="none" stroke="#FFFFFF" stroke-width="3.2"'
        ' stroke-linecap="round" stroke-linejoin="round"/></svg>', height, height,
    )
    bang = svg_image(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 25">'
        '<rect x="1" y="0" width="7" height="16" rx="3.5" fill="#C8460F"/>'
        '<circle cx="4.5" cy="21.2" r="3.6" fill="#3B3BE6"/></svg>', round(word * 0.36), word,
    )
    return (
        f'<span class="ci-logo" role="img" aria-label="Called it!">{bubble}'
        f'<span class="ci-logo-word" style="font-size:{word}px">CALLED IT</span>{bang}</span>'
    )


def money(amount: Decimal) -> str:
    """Format coins with thousands separators; cents only when there are any."""
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


def signed(amount: Decimal) -> str:
    """Format a win or loss of coins, like '+28', '−40' or '±0'."""
    if amount > 0:
        return f"+{money(amount)}"
    if amount < 0:
        return f"{MINUS}{money(-amount)}"
    return "±0"


def odds_text(odds: Optional[float]) -> str:
    """Format odds like '2.40×', or a dash when nobody backed that side."""
    return f"{odds:.2f}×" if odds else "—"


def when(moment: datetime) -> str:
    """Format a time in the app's timezone, like 'Fri 20 Nov, 19:00'."""
    return moment.astimezone(APP_TIMEZONE).strftime("%a %d %b, %H:%M")


def day(moment: datetime) -> str:
    """Format a date in the app's timezone, like 'Sun 15 Nov'."""
    return moment.astimezone(APP_TIMEZONE).strftime("%a %d %b")


def pill(text: str, kind: str) -> str:
    """Return a small status pill; kind is a ci-pill-* style (open, waiting, won ...)."""
    return f'<span class="ci-pill ci-pill-{kind}">{esc(text)}</span>'


def side_tag(side: str) -> str:
    """Return 'YES' in blue or 'NO' in orange-red."""
    return f'<b class="ci-{side}">{esc(side.upper())}</b>'


def admin_badge() -> str:
    """Return the small black ADMIN badge."""
    return f'<span class="ci-admin">{CROWN}ADMIN</span>'


def avatar(name: str, is_me: bool = False) -> str:
    """Return a round initial; the viewer's own is black, others get a fixed pastel."""
    colour = AVATAR_COLOURS[sum(map(ord, name)) % len(AVATAR_COLOURS)]
    extra = " ci-avatar-me" if is_me else ""
    initial = esc(name[:1].upper() or "?")
    return f'<span class="ci-avatar{extra}" style="background:{colour}">{initial}</span>'


def who(name: str, is_me: bool, is_admin: bool = False) -> str:
    """Return avatar plus name ('You' for the viewer), with the admin badge if needed."""
    label = "You" if is_me else esc(name)
    badge = admin_badge() if is_admin else ""
    return f'<span class="ci-who">{avatar(name, is_me)}<span>{label}{badge}</span></span>'


def odds_boxes(odds_yes: Optional[float], odds_no: Optional[float], final: bool = False,
               winner: Optional[str] = None) -> str:
    """Return the YES and NO odds boxes; final odds are drawn neutral, the winner outlined."""
    boxes = []
    for side, odds in (("yes", odds_yes), ("no", odds_no)):
        classes = f"ci-odd ci-odd-{side}" + (" ci-odd-final" if final else "")
        classes += " ci-odd-win" if winner == side else ""
        number = f"{odds:.2f}" if odds else "—"
        boxes.append(f'<div class="{classes}"><b>{number}</b><span>{side.upper()}</span></div>')
    return f'<div class="ci-odds">{"".join(boxes)}</div>'


def stake_bar(stake_yes: Decimal, stake_no: Decimal, middle: str = "", grey: bool = False) -> str:
    """Return the labels and the two-colour bar showing how the pool splits."""
    pool = stake_yes + stake_no
    mid = f'<span class="ci-split-mid">{esc(middle)}</span>' if middle else ""
    if pool == 0:
        return (f'<div class="ci-split"><span class="ci-split-mid">No bets yet</span>{mid}</div>'
                '<div class="ci-bar"><div class="ci-bar-empty"></div></div>')
    yes = round(stake_yes / pool * 100)
    labels = (
        f'<div class="ci-split"><span class="ci-yes">YES {yes}% · {money(stake_yes)} coins'
        f'</span>{mid}<span class="ci-no">{money(stake_no)} coins · {100 - yes}% NO</span></div>'
    )
    parts = []
    if stake_yes:
        parts.append(f'<div class="ci-bar-yes" style="flex:{stake_yes}"></div>')
    if stake_no:
        parts.append(f'<div class="ci-bar-no" style="flex:{stake_no}"></div>')
    grey_class = " ci-bar-grey" if grey else ""
    return f'{labels}<div class="ci-bar{grey_class}">{"".join(parts)}</div>'


def option_row(name: str, odds: Optional[float], share: Optional[float], stake: Decimal,
               members: int, my_stake: Decimal, up_to: bool = False, won: bool = False) -> str:
    """Return one option of a names event: name, who is in, its pool share as a bar, and its odds.

    odds is None while nobody backed it and share is None while the pool is
    empty; both then show a dash. With up_to (several can win) the odds read
    'up to'. A winning option is outlined and tagged 'Won'.
    """
    percent = None if share is None else round(share * 100)
    sub = [f"{members} member{'s' if members != 1 else ''} · {money(stake)} coins"
           if members else "no bets yet"]
    if my_stake:
        sub.append(f"you {money(my_stake)}")
    tag = pill("Won", "won") if won else ""
    prefix = "<small>up to</small> " if up_to and odds else ""
    return (
        f'<div class="ci-opt{" ci-opt-win" if won else ""}">'
        f'<div class="ci-opt-name">{esc(name)}<span class="ci-opt-sub">{tag}{" · ".join(sub)}</span></div>'
        f'<div class="ci-opt-bar"><div style="width:{percent or 0}%"></div></div>'
        f'<div class="ci-opt-share">{"—" if percent is None else f"{percent}%"}</div>'
        f'<div class="ci-opt-odds">{prefix}{odds_text(odds)}</div></div>'
    )


def heading(title: str, kicker: str) -> str:
    """Return the big page title with the small grey line under it."""
    return f'<div class="ci-h1">{esc(title)}</div><div class="ci-kicker">{esc(kicker)}</div>'

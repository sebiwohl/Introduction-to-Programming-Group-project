"""The Group page: members and balances, the invite code, the rules and the account box."""

import streamlit as st

from src.models import ROLE_ADMIN
from src.service import PageData
from src.ui import style
from src.ui.account import log_out

RULES = (
    "Odds come from the group: winners split the whole pool in proportion to their stakes.",
    "If only one side has bets, everyone gets their stake back.",
    "Betting stops at the close time. Then the creator or the admin sets the result.",
    "Coins are virtual. Nobody can bet more than their balance.",
)


def _members_card(page: PageData) -> str:
    """Return the list of members with balance and open bets; the admin comes first."""
    rows = sorted(page.leaderboard, key=lambda r: (not r.is_admin, r.username.casefold()))
    lines = []
    for row in rows:
        is_me = row.username == page.user.username
        name = "You" if is_me else style.esc(row.username)
        badge = style.admin_badge() if row.is_admin else ""
        role = "Group admin" if row.is_admin else "Member"
        bets = f"{row.open_bets} open bet{'' if row.open_bets == 1 else 's'}"
        lines.append(
            f'<div class="ci-member">{style.avatar(row.username, is_me)}<div><b>{name}</b>{badge}'
            f'<div class="ci-muted" style="font-size:.75rem">{role}</div></div>'
            f'<div class="ci-member-money"><b>{style.money(row.balance)} coins</b>'
            f'<div class="ci-muted" style="font-size:.75rem">{bets}</div></div></div>'
        )
    return (
        f'<div class="ci-card"><div class="ci-row" style="margin-bottom:6px">'
        f'<span class="ci-label">Members · {len(rows)}</span>'
        f'<span class="ci-muted">Balance · open bets</span></div>{"".join(lines)}</div>'
    )


def _invite_card(page: PageData) -> None:
    """Draw the black invite card; the code block has Streamlit's copy button."""
    with st.container(key="invite"):
        style.show('<div class="ci-kicker">Invite friends</div>')
        st.code(page.group.invite_code, language=None)
        style.show(
            '<div style="font-size:.85rem;color:#D6D6DC">Friends enter this code after signing up. '
            f"New members start with {style.money(page.group.start_balance)} coins.</div>"
        )


def _rules_card() -> str:
    """Return the card with the group rules the app enforces today."""
    rules = "".join(f"<p>{style.esc(rule)}</p>" for rule in RULES)
    return (f'<div class="ci-card ci-rules"><div class="ci-label" style="margin-bottom:10px">'
            f"Group rules</div>{rules}</div>")


def _account_card(page: PageData) -> None:
    """Draw the account box with the username, role and the log-out button."""
    role = "admin" if page.user.role == ROLE_ADMIN else "member"
    with st.container(key="card_account"):
        style.show(f'<div class="ci-label">Your account</div><div style="margin:8px 0 2px"><b>'
                   f"Signed in as {style.esc(page.user.username)}</b></div>"
                   f'<div class="ci-muted">You are a {role} of this group.</div>')
        if st.button("Log out", key="logout_group"):
            log_out()


def group_page(page: PageData) -> None:
    """Draw the Group page."""
    admin = next((r.username for r in page.leaderboard if r.is_admin), "")
    kicker = f"{len(page.leaderboard)} members"
    if admin:
        kicker += f" · admin: {admin}"
    kicker += f" · everyone started with {style.money(page.group.start_balance)} coins"
    style.show(style.heading(page.group.name, kicker))
    st.write("")
    left, right = st.columns([2, 1], gap="large")
    style.show(where=left, markup=_members_card(page))
    with right:
        _invite_card(page)
        style.show(_rules_card())
        _account_card(page)

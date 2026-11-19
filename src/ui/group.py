"""The Group page: members and balances, the invite code, the rules and the account box."""

from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from src import auth, balances, members
from src.betting import BettingError
from src.models import ROLE_ADMIN
from src.storage import StorageError
from src.service import PageData
from src.ui import style
from src.ui.account import log_out
from src.ui.actions import attempt

RULES = (
    "Odds come from the group: winners split the whole pool in proportion to their stakes.",
    "If only one side has bets, everyone gets their stake back.",
    "Names events: everyone who backed a winning name shares the whole pool. "
    "If nobody backed a winner, everyone gets their stake back.",
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
            "New members start at 0 and get coins with the next top-up.</div>"
        )


def _top_up_form(page: PageData) -> None:
    """Draw the admin's form that gives every member the same amount of coins.

    The permission is checked again in balances.top_up; this only hides the
    form from members.
    """
    if page.user.role != ROLE_ADMIN:
        return
    with st.form("top_up"):
        style.show('<div class="ci-label">Top up everyone</div>')
        st.caption("Every member gets the same amount, new members included.")
        amount = st.number_input("Coins for each member", min_value=1, value=100, step=50)
        if st.form_submit_button("Top up", type="primary"):
            attempt(lambda: balances.top_up(page.user.id, page.group.id, Decimal(int(amount)),
                                             datetime.now(timezone.utc)),
                    f"Everyone got {int(amount)} coins.")
            st.rerun()  # shows the success or the error message


ADMIN_ACTIONS = ("Restrict betting", "Lift restriction", "Remove from group", "Make admin")


def _now() -> datetime:
    """Return the current time with a timezone."""
    return datetime.now(timezone.utc)


def _run_admin_action(page: PageData, member: members.MemberInfo, action: str) -> None:
    """Run the chosen admin action on the member; the rules are checked in the logic."""
    me, group_id = page.user.id, page.group.id
    steps = {
        "Restrict betting": (lambda: members.set_restricted(me, group_id, member.user_id, True,
                                                            _now()),
                             f"{member.username} can only view now."),
        "Lift restriction": (lambda: members.set_restricted(me, group_id, member.user_id, False,
                                                            _now()),
                             f"{member.username} can bet again."),
        "Remove from group": (lambda: members.remove_member(me, group_id, member.user_id, _now()),
                              f"{member.username} was removed."),
        "Make admin": (lambda: auth.hand_over_admin(me, group_id, member.user_id, _now()),
                       f"{member.username} is the admin now."),
    }
    action_fn, success = steps[action]
    attempt(action_fn, success)
    st.rerun()  # shows the success or the error message


def _manage_members(page: PageData) -> None:
    """Draw the admin's form to restrict, remove or promote a member, or delete the group."""
    if page.user.role != ROLE_ADMIN:
        return
    try:
        others = [m for m in members.group_members(page.user.id, page.group.id)
                  if m.user_id != page.user.id]
    except (BettingError, StorageError) as error:
        st.error(str(error))
        return
    if not others:
        _delete_group_form(page)
        return
    with st.form("manage_members"):
        style.show('<div class="ci-label">Manage members</div>')
        st.caption("Remove only works for members with no open bets and a result of 0 this "
                   "period. Restrict someone first if they still have open bets.")
        member = st.selectbox("Member", others, format_func=lambda m: m.username
                              + (" (restricted)" if m.restricted else ""))
        action = st.radio("Action", ADMIN_ACTIONS, horizontal=True)
        if st.form_submit_button("Apply"):
            _run_admin_action(page, member, action)


def _delete_group_form(page: PageData) -> None:
    """Draw the delete button the admin sees once they are the last member."""
    with st.form("delete_group"):
        style.show('<div class="ci-label">Delete the group</div>')
        st.caption("You are the last member. Deleting removes the group and all its events "
                   "for good.")
        sure = st.checkbox("Yes, delete this group and everything in it")
        if st.form_submit_button("Delete group"):
            if not sure:
                st.error("Tick the box to confirm.")
            else:
                attempt(lambda: members.delete_group(page.user.id, page.group.id),
                        "The group was deleted.")
                st.rerun()  # shows the success or the error message


def _leave_form(page: PageData) -> None:
    """Draw the form to leave the group; the rules are checked in members.leave_group."""
    with st.form("leave_group"):
        style.show('<div class="ci-label">Leave the group</div>')
        st.caption("Possible with no open bets and a result of 0 this period. "
                   "The admin hands over the role first.")
        sure = st.checkbox("Yes, I want to leave")
        if st.form_submit_button("Leave group"):
            if not sure:
                st.error("Tick the box to confirm.")
            else:
                attempt(lambda: members.leave_group(page.user.id, page.group.id, _now()),
                        f"You left {page.group.name}.")
                st.rerun()  # shows the success or the error message


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
    style.show(style.heading(page.group.name, kicker))
    st.write("")
    left, right = st.columns([2, 1], gap="large")
    style.show(where=left, markup=_members_card(page))
    with left:
        _top_up_form(page)
        _manage_members(page)
    with right:
        _invite_card(page)
        style.show(_rules_card())
        _account_card(page)
        _leave_form(page)

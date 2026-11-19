"""The Group page: members and balances, the invite code, How it works and leaving the group."""

import time
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from src import auth, balances, members
from src.betting import BettingError
from src.models import ROLE_ADMIN
from src.storage import StorageError
from src.service import PageData
from src.ui import style
from src.ui.actions import attempt

# The "How it works" box: one page per topic, turned with the side arrows or every few seconds.
HOW_IT_WORKS = (
    ("Group", (
        "Everyone joins with 0 coins. The admin tops up every member at once with the same "
        "amount, new members included.",
        "You need coins to create events or bet, and a balance never goes below 0.",
        "Repayment on the Leaderboard shows who is up or down since the last repayment and "
        "who pays whom. The paying happens outside the app.",
        "Once everyone has paid, the admin presses Repaid: a new period starts with a fresh "
        "top-up. Open bets carry over.",
        "You can leave with no open bets and a result of 0 since the last repayment.",
    )),
    ("Betting", (
        "There is no bookmaker: odds = the whole pool ÷ the stakes on that side. Winners split "
        "the whole pool in proportion to their stakes.",
        "Three types: Yes / no, Names (2 to 10 names, one or several can win) and "
        "Over / under a number.",
        "Betting stops at the close time. After the event date the creator or the admin sets "
        "the result, and that cannot be undone.",
        "If only one side has bets or nobody backed the winner, everyone gets their stake back. "
        "Exactly on an over / under line refunds too.",
        "The creator or the admin can delete an event up to 3 hours before betting closes, "
        "with every stake refunded. After that, only a poll can remove it.",
    )),
    ("Moderation", (
        "Something fishy? Flag the event in its ⋮ menu. One flag per member.",
        "When 25% of the members (at least 2) flag it, betting pauses and the group votes in a "
        "poll for 1 hour (shorter if betting closes sooner).",
        "If more than half of the members flag it, the event is deleted at once and every "
        "stake is refunded.",
        "Your flag counts as a Delete vote; you can switch it to Keep once. Everyone else "
        "has one vote.",
        "More than half of the votes for Delete removes the event with refunds. A tie or no "
        "votes keeps it, clears the flags and reopens betting.",
    )),
    ("Admin", (
        "Shares the invite code, tops up everyone and confirms Repaid.",
        "Can open a poll directly, set the result of any event and delete any event (same "
        "3-hour rule).",
        "Can restrict a member's betting and lift it again, and remove members with no open "
        "bets and a result of 0.",
        "Hands the admin role to another member before leaving. As the last member, the "
        "admin can delete the group.",
    )),
)
HOWTO_PAGE_KEY, HOWTO_TURNED_KEY = "howto_page", "howto_turned_at"
HOWTO_SECONDS = 12  # the box turns to the next page this often, unless someone just clicked


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


@st.dialog("Leave the group?")
def _confirm_leave(page: PageData) -> None:
    """Ask before leaving the group; the rules are checked in members.leave_group."""
    style.show(f'<div style="font-weight:800;font-size:1.05rem">'
               f"Do you really want to leave {style.esc(page.group.name)}?</div>"
               '<p class="ci-muted" style="margin-top:8px">To come back you need the '
               "invite code again.</p>")
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("Cancel", key="leave_cancel", width="stretch"):
        st.rerun()
    if confirm_col.button("Yes, leave", key="leave_confirm", type="primary", width="stretch"):
        attempt(lambda: members.leave_group(page.user.id, page.group.id, _now()),
                f"You left {page.group.name}.")
        st.rerun()  # shows the success or the error message


def _leave_box(page: PageData) -> None:
    """Draw the Leave the group box; the red button asks for confirmation in a pop-up."""
    with st.container(key="card_leave"):
        style.show('<div class="ci-label">Leave the group</div>')
        st.caption("Possible with no open bets and a result of 0 since the last repayment. "
                   "The admin hands over the role first.")
        if st.button("Leave group", key="leave_group_btn", icon=":material/logout:"):
            _confirm_leave(page)


def _turn_howto(step: int) -> None:
    """Go to the next (1) or previous (-1) How it works page and restart the timer."""
    page = st.session_state.get(HOWTO_PAGE_KEY, 0)
    st.session_state[HOWTO_PAGE_KEY] = (page + step) % len(HOW_IT_WORKS)
    st.session_state[HOWTO_TURNED_KEY] = time.monotonic()


def _howto_markup(index: int) -> str:
    """Return the inside of the How it works box for one page, with the page dots."""
    title, lines = HOW_IT_WORKS[index]
    items = "".join(f"<p>{style.esc(line)}</p>" for line in lines)
    dots = "".join(f'<span class="ci-dot{" ci-dot-on" if i == index else ""}"></span>'
                   for i in range(len(HOW_IT_WORKS)))
    return (f'<div class="ci-label">How it works</div>'
            f'<div class="ci-howto-title">{style.esc(title)}</div>'
            f'<div class="ci-howto">{items}</div><div class="ci-dots">{dots}</div>')


@st.fragment(run_every=HOWTO_SECONDS)
def _how_it_works() -> None:
    """Draw the How it works box: the arrows next to the page dots turn the page.

    A fragment with its own timer: only this box is redrawn, and it turns to the
    next page by itself when nobody has clicked for HOWTO_SECONDS.
    """
    turned = st.session_state.setdefault(HOWTO_TURNED_KEY, time.monotonic())
    if time.monotonic() - turned >= HOWTO_SECONDS - 0.5:
        _turn_howto(1)
    with st.container(key="card_howto"):
        st.button(":material/chevron_left:", key="howto_prev", help="Previous",
                  on_click=_turn_howto, args=(-1,))
        st.button(":material/chevron_right:", key="howto_next", help="Next",
                  on_click=_turn_howto, args=(1,))
        style.show(_howto_markup(st.session_state.get(HOWTO_PAGE_KEY, 0)))


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
        _how_it_works()
        _leave_box(page)

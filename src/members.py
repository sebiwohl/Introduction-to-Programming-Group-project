"""Managing group members: restricting, leaving, removing and deleting the group.

Requirements 2.1, 2.2 and 2.4. A member can leave, or be removed by the admin,
only when they are settled: no open bets and a result of exactly 0 this period
(no debt and no winnings), so every repayment still adds up. Someone who is up
or down waits for the next repayment, which brings everyone to 0. The admin
hands over the role before leaving; as the last member they delete the group.

Permissions come from auth.require_permission(); changes are saved through
storage.transaction().
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List

from src import auth, balances, storage
from src.auth import AuthError
from src.convert import cents_to_money
from src.models import ROLE_ADMIN
from src.notifications import add_notification

# Records that point at their group directly.
GROUP_SECTIONS = ("memberships", "topups", "repayments", "notifications")


def set_restricted(
    admin_id: str, group_id: str, member_id: str, restricted: bool, now: datetime, path=None
) -> None:
    """Restrict a member of the group to viewing only, or lift the restriction.

    Only the group's admin may do this, and not to themselves. The member is
    notified. Setting the state they already have changes nothing.
    """
    if not isinstance(restricted, bool):
        raise AuthError("Choose restrict or lift.")
    with storage.transaction(path) as data:
        admin = auth.user_in_group(data, admin_id, group_id)
        auth.require_permission(admin, auth.MANAGE_MEMBERS)
        if member_id == admin_id:
            raise AuthError("The admin cannot restrict themselves.")
        membership = next((m for m in data["memberships"]
                           if m["user_id"] == member_id and m["group_id"] == group_id), None)
        if membership is None:
            raise AuthError("That person is not a member of this group.")
        if membership["restricted"] == restricted:
            return
        membership["restricted"] = restricted
        group_name = next(g["name"] for g in data["groups"] if g["id"] == group_id)
        text = (f"The admin restricted you in {group_name}: you can only view for now."
                if restricted else f"You can bet in {group_name} again.")
        add_notification(data, member_id, group_id, text, now)


@dataclass
class MemberInfo:
    """One member as the Group tab's admin form needs them."""

    user_id: str
    username: str
    role: str
    restricted: bool


def group_members(user_id: str, group_id: str, path=None) -> List[MemberInfo]:
    """Return the group's members (sorted by name) for someone in the group."""
    data = storage.load_data(path)
    auth.require_permission(auth.user_in_group(data, user_id, group_id), auth.VIEW_FEED)
    names = {u["id"]: u["username"] for u in data["users"]}
    found = [MemberInfo(m["user_id"], names[m["user_id"]], m["role"], m["restricted"])
             for m in data["memberships"] if m["group_id"] == group_id]
    return sorted(found, key=lambda m: m.username.casefold())


def _membership(data: dict, user_id: str, group_id: str) -> dict:
    """Return the stored membership, or raise AuthError."""
    membership = next((m for m in data["memberships"]
                       if m["user_id"] == user_id and m["group_id"] == group_id), None)
    if membership is None:
        raise AuthError("That person is not a member of this group.")
    return membership


def _check_settled(data: dict, membership: dict, who: str) -> None:
    """Raise AuthError unless the member has no open bets and a result of exactly 0.

    `who` starts the message: "You" or the member's name.
    """
    stake = balances.open_stakes(data, membership["group_id"]).get(membership["user_id"], 0)
    if stake:
        raise AuthError(f"{who} still {'have' if who == 'You' else 'has'} open bets. "
                        "Wait until they are settled (the admin can restrict betting meanwhile).")
    result = membership["balance"] + stake - membership["received"]
    if result < 0:
        raise AuthError(f"{who} {'are' if who == 'You' else 'is'} down "
                        f"{cents_to_money(-result)} coins this period. "
                        "Settle it at the next repayment first.")
    if result > 0:
        raise AuthError(f"{who} {'are' if who == 'You' else 'is'} up "
                        f"{cents_to_money(result)} coins this period. "
                        "Wait for the next repayment, then it is possible.")


def _group_name(data: dict, group_id: str) -> str:
    """Return the group's name."""
    return next(g["name"] for g in data["groups"] if g["id"] == group_id)


def leave_group(user_id: str, group_id: str, now: datetime, path=None) -> None:
    """Leave the group when settled: no open bets and a result of exactly 0.

    The admin must hand over the role first; if they are the last member, they
    delete the group instead. The admin is notified when someone leaves.
    """
    with storage.transaction(path) as data:
        user = auth.user_in_group(data, user_id, group_id)
        others = [m for m in data["memberships"]
                  if m["group_id"] == group_id and m["user_id"] != user_id]
        if user.role == ROLE_ADMIN:
            if not others:
                raise AuthError("You are the last member: delete the group instead.")
            raise AuthError("Hand over the admin role to another member before leaving.")
        membership = _membership(data, user_id, group_id)
        _check_settled(data, membership, "You")
        data["memberships"].remove(membership)
        for admin in others:
            if admin["role"] == ROLE_ADMIN:
                add_notification(data, admin["user_id"], group_id,
                                 f"{user.username} left {_group_name(data, group_id)}.", now)


def remove_member(admin_id: str, group_id: str, member_id: str, now: datetime,
                  path=None) -> None:
    """Let the admin remove a settled member (no open bets, result exactly 0).

    The admin cannot remove themselves (they leave instead). The removed
    member is notified.
    """
    with storage.transaction(path) as data:
        admin = auth.user_in_group(data, admin_id, group_id)
        auth.require_permission(admin, auth.MANAGE_MEMBERS)
        if member_id == admin_id:
            raise AuthError("The admin cannot remove themselves.")
        membership = _membership(data, member_id, group_id)
        name = next(u["username"] for u in data["users"] if u["id"] == member_id)
        _check_settled(data, membership, name)
        data["memberships"].remove(membership)
        add_notification(data, member_id, group_id,
                         f"The admin removed you from {_group_name(data, group_id)}.", now)


def delete_group(admin_id: str, group_id: str, path=None) -> None:
    """Delete the group and everything in it; only its admin, as its last member, may.

    Other members must leave first (or the admin hands over the role and leaves).
    """
    with storage.transaction(path) as data:
        admin = auth.user_in_group(data, admin_id, group_id)
        auth.require_permission(admin, auth.MANAGE_MEMBERS)
        members = [m for m in data["memberships"] if m["group_id"] == group_id]
        if len(members) > 1:
            raise AuthError("Only the last member can delete the group.")
        _remove_group_records(data, group_id)


def _remove_group_records(data: dict, group_id: str) -> None:
    """Remove the group and every record that belongs to it from `data`."""
    event_ids = {e["id"] for e in data["events"] if e["group_id"] == group_id}
    data["bets"] = [b for b in data["bets"] if b["event_id"] not in event_ids]
    data["polls"] = [p for p in data["polls"] if p["event_id"] not in event_ids]
    data["events"] = [e for e in data["events"] if e["group_id"] != group_id]
    for section in GROUP_SECTIONS:
        data[section] = [r for r in data[section] if r["group_id"] != group_id]
    data["groups"] = [g for g in data["groups"] if g["id"] != group_id]

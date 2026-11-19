"""Managing group members: restricting betting and deleting the group (requirements 2.1, 2.2).

Leaving a group and removing a member come later, with the balance rules,
because they need each member's result for the current period.
Permissions come from auth.require_permission(); changes are saved through
storage.transaction().
"""

from datetime import datetime

from src import auth, storage
from src.auth import AuthError
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

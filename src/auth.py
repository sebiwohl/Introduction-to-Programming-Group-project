"""Accounts, groups and permissions.

- Passwords are stored only as salted PBKDF2-SHA256 hashes, never as text.
- Login errors never say whether the username or the password was wrong.
- require_permission() is the one shared permission check (requirements 2.1).

Changes are saved through storage.transaction(), like in service.py.
Errors are AuthError, a kind of BettingError, so the UI can catch one type.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from src import storage
from src.betting import BettingError, can_access_event
from src.convert import (
    find_membership, group_from_dict, group_to_dict, membership_from_dict, membership_to_dict,
    user_from_dict, user_to_dict,
)
from src.notifications import add_notification, notify_group
from src.models import ROLE_ADMIN, ROLE_MEMBER, Event, Group, Membership, User

# Password hashing. The iteration count is stored inside each hash, so it can be
# raised later without breaking existing accounts.
HASH_NAME = "pbkdf2_sha256"
HASH_ITERATIONS = 600_000
SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 8
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 30
MAX_GROUP_NAME_LENGTH = 50

# Invite codes: 6 characters, without look-alikes such as 0/O and 1/I.
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_LENGTH = 6

LOGIN_FAILED = "Wrong username or password."
TOP_UP_REQUIRED = "Top-up required before creating events."
RESTRICTED = "The admin has restricted you in this group: you can only view until they lift it."

# Actions for require_permission(), from the roles table in requirements 2.1.
VIEW_FEED = "view_feed"
CREATE_EVENT = "create_event"
PLACE_BET = "place_bet"
FLAG_EVENT = "flag_event"
VOTE_POLL = "vote_poll"
RESOLVE_EVENT = "resolve_event"
OPEN_POLL = "open_poll"
DELETE_EVENT = "delete_event"
MANAGE_MEMBERS = "manage_members"
TRANSFER_ADMIN = "transfer_admin"
TOP_UP = "top_up"
REPAY = "repay"

MEMBER_ACTIONS = {VIEW_FEED, CREATE_EVENT, PLACE_BET, FLAG_EVENT, VOTE_POLL}
ADMIN_ONLY_ACTIONS = {OPEN_POLL, MANAGE_MEMBERS, TRANSFER_ADMIN, TOP_UP, REPAY}
CREATOR_OR_ADMIN_ACTIONS = {RESOLVE_EVENT: "resolve", DELETE_EVENT: "delete"}


class AuthError(BettingError):
    """Raised when sign-up, login, a group action or a permission check fails."""


# --- passwords ----------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a salted hash like 'pbkdf2_sha256$600000$<salt hex>$<hash hex>'."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ITERATIONS)
    return f"{HASH_NAME}${HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Return True if the password matches the stored hash (constant-time compare)."""
    try:
        name, iterations, salt_hex, digest_hex = stored.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        rounds = int(iterations)
    except (AttributeError, ValueError):
        return False
    if name != HASH_NAME:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return hmac.compare_digest(digest, expected)


# A real hash to check against when the username does not exist, so a failed
# login takes the same time either way. Built on first use.
_dummy_hash: Optional[str] = None


def _waste_time_like_a_login(password: str) -> None:
    """Run one hash check so unknown usernames are not faster to reject."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not a real password")
    verify_password(password, _dummy_hash)


# --- input checks -----------------------------------------------------------

def _clean_username(username: str) -> str:
    """Return the trimmed username, or raise AuthError if it is not allowed."""
    if not isinstance(username, str):
        raise AuthError("Username must be text.")
    username = username.strip()
    if not MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH:
        raise AuthError(
            f"Username must be {MIN_USERNAME_LENGTH} to {MAX_USERNAME_LENGTH} characters."
        )
    return username


def _check_password_rules(password: str) -> None:
    """Raise AuthError if the password is too short or not text."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def _same_username(a: str, b: str) -> bool:
    """Compare usernames the way storage does: trimmed and ignoring case."""
    return a.strip().casefold() == b.strip().casefold()


def _find_user(data: dict, user_id: str) -> dict:
    """Return the stored user dict, or raise AuthError."""
    for user in data["users"]:
        if user["id"] == user_id:
            return user
    raise AuthError("Unknown user.")


# --- accounts -----------------------------------------------------------------

def sign_up(username: str, password: str, path=None) -> User:
    """Create an account without a group and return it.

    The username is trimmed and must be unique (ignoring case). The password
    must have at least MIN_PASSWORD_LENGTH characters.
    """
    username = _clean_username(username)
    _check_password_rules(password)
    password_hash = hash_password(password)  # slow, so done outside the lock
    with storage.transaction(path) as data:
        if any(_same_username(u["username"], username) for u in data["users"]):
            raise AuthError("That username is taken.")
        user = User(id=uuid.uuid4().hex, username=username,
                    password_hash=password_hash, balance=Decimal("0"))
        data["users"].append(user_to_dict(user))
    return user


def log_in(username: str, password: str, path=None) -> User:
    """Return the user if username and password match, else raise AuthError.

    The error message is the same for an unknown username and a wrong password.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        raise AuthError(LOGIN_FAILED)
    data = storage.load_data(path)
    for stored in data["users"]:
        if _same_username(stored["username"], username):
            if verify_password(password, stored["password_hash"]):
                return user_from_dict(stored, find_membership(data["memberships"], stored["id"]))
            raise AuthError(LOGIN_FAILED)
    _waste_time_like_a_login(password)
    raise AuthError(LOGIN_FAILED)


# --- groups -------------------------------------------------------------------

def _group_membership(data: dict, user_id: str, group_id: str) -> Optional[dict]:
    """Return the user's stored membership in this group, or None."""
    return next((m for m in data["memberships"]
                 if m["user_id"] == user_id and m["group_id"] == group_id), None)


def _add_membership(data: dict, user_id: str, group_id: str, role: str) -> None:
    """Store a new membership for the user, unless they are already in this group.

    A user can be in several groups (requirements 2.3), but only once in each.
    """
    if _group_membership(data, user_id, group_id) is not None:
        raise AuthError("You are already in this group.")
    membership = Membership(id=uuid.uuid4().hex, user_id=user_id, group_id=group_id,
                            role=role)
    data["memberships"].append(membership_to_dict(membership))


def _new_invite_code(data: dict) -> str:
    """Return a random invite code that no group uses yet."""
    used = {g["invite_code"] for g in data["groups"]}
    while True:
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
        if code not in used:
            return code


def create_group(user_id: str, name: str, path=None) -> Group:
    """Create a group and make the user its admin, with a balance of 0.

    There is no starting balance (requirements 2.2): once everyone has joined,
    the admin tops up all members at once. The user may be in other groups.
    """
    if not isinstance(name, str) or not name.strip():
        raise AuthError("Group name cannot be empty.")
    name = name.strip()
    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise AuthError(f"Group name can be at most {MAX_GROUP_NAME_LENGTH} characters.")
    with storage.transaction(path) as data:
        _find_user(data, user_id)
        group = Group(id=uuid.uuid4().hex, name=name, admin_id=user_id,
                      invite_code=_new_invite_code(data))
        _add_membership(data, user_id, group.id, ROLE_ADMIN)
        data["groups"].append(group_to_dict(group))
    return group


def join_group(user_id: str, invite_code: str, path=None,
               now: Optional[datetime] = None) -> Group:
    """Add the user to the group with this invite code as a member.

    The code ignores case and surrounding spaces. The new member starts at 0
    and gets coins with the admin's next top-up (requirements 2.4). The user
    may already be in other groups, but not in this one. The other members see
    it in their activity log; now defaults to the current time.
    """
    code = invite_code.strip().upper() if isinstance(invite_code, str) else ""
    with storage.transaction(path) as data:
        user = _find_user(data, user_id)
        group = next((g for g in data["groups"] if g["invite_code"] == code), None)
        if group is None:
            raise AuthError("No group has that invite code.")
        joined = group_from_dict(group)
        _add_membership(data, user_id, joined.id, ROLE_MEMBER)
        notify_group(data, joined.id, f"{user['username']} joined {joined.name}.",
                     now or datetime.now(timezone.utc), skip=[user_id])
    return joined


def user_groups(user_id: str, path=None) -> List[Tuple[Group, Membership]]:
    """Return each group the user is in with their membership, sorted by group name."""
    data = storage.load_data(path)
    groups = {g["id"]: group_from_dict(g) for g in data["groups"]}
    pairs = [(groups[m["group_id"]], membership_from_dict(m))
             for m in data["memberships"] if m["user_id"] == user_id]
    return sorted(pairs, key=lambda pair: (pair[0].name.casefold(), pair[0].id))


def user_in_group(data: dict, user_id: str, group_id: str) -> User:
    """Return the user with their group, role and balance in this group.

    A group the user is not in is reported as not found, so its id reveals nothing.
    """
    membership = _group_membership(data, user_id, group_id)
    if membership is None:
        raise AuthError("Group not found.")
    return user_from_dict(_find_user(data, user_id), membership)


def hand_over_admin(
    admin_id: str, group_id: str, new_admin_id: str, now: datetime, path=None
) -> None:
    """Make another member of the group its admin; the old admin becomes a member.

    Only the group's admin may do this (requirements 2.1 and 2.2). The new
    admin gets a notification.
    """
    with storage.transaction(path) as data:
        admin = user_in_group(data, admin_id, group_id)
        require_permission(admin, TRANSFER_ADMIN)
        if new_admin_id == admin_id:
            raise AuthError("You are already the admin.")
        new_membership = _group_membership(data, new_admin_id, group_id)
        if new_membership is None:
            raise AuthError("The new admin must be a member of this group.")
        _group_membership(data, admin_id, group_id)["role"] = ROLE_MEMBER
        new_membership["role"] = ROLE_ADMIN
        group = next(g for g in data["groups"] if g["id"] == group_id)
        group["admin_id"] = new_admin_id
        add_notification(data, new_admin_id, group_id,
                         f"You are now the admin of {group['name']}.", now)
        names = {u["id"]: u["username"] for u in data["users"]}
        notify_group(data, group_id, f"{names[admin_id]} handed the admin role to "
                     f"{names[new_admin_id]}.", now, skip=[admin_id, new_admin_id])


# --- permissions --------------------------------------------------------------

def is_group_admin(user: User, group_id: str) -> bool:
    """Return True if the user is the admin of this group."""
    return user.role == ROLE_ADMIN and user.group_id == group_id


def require_permission(user: User, action: str, event: Optional[Event] = None) -> None:
    """Raise AuthError unless the user may do `action` (requirements 2.1).

    Every action needs a group. When an event is given, it must be in one of
    the user's groups. Resolving and deleting an event are allowed for its
    creator and the group admin; open_poll, manage_members and transfer_admin
    are admin-only, like top_up and repay. A restricted member may only view (requirements
    2.2), and a member with a balance of 0 cannot create events (2.4).
    Time limits (such as deleting only more than 3 hours before betting closes)
    are checked where the action happens.
    """
    if not user.group_id:
        raise AuthError("Join or create a group first.")
    if event is not None and not can_access_event(user, event):
        raise AuthError("Event not found.")
    if user.restricted and action != VIEW_FEED:
        raise AuthError(RESTRICTED)
    if action == CREATE_EVENT and user.balance <= 0:
        raise AuthError(TOP_UP_REQUIRED)
    if action in MEMBER_ACTIONS:
        return
    if action in CREATOR_OR_ADMIN_ACTIONS:
        verb = CREATOR_OR_ADMIN_ACTIONS[action]
        if event is None:
            raise AuthError(f"Needs an event to {verb}.")
        if user.id == event.creator_id or is_group_admin(user, event.group_id):
            return
        raise AuthError(f"Only the creator or the group admin can {verb} this event.")
    if action in ADMIN_ONLY_ACTIONS:
        group_id = event.group_id if event is not None else user.group_id
        if is_group_admin(user, group_id):
            return
        raise AuthError("Only the group admin can do this.")
    raise AuthError(f"Unknown action {action!r}.")

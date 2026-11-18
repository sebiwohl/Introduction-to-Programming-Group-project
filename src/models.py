"""Data models for the betting app (see docs/product-requirements.md, section 4.2)."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

# Money is a Decimal with at most 2 decimal places, e.g. Decimal("12.50").
CENT = Decimal("0.01")

# Allowed values, kept as constants so other modules never hard-code strings.
SIDE_YES = "yes"
SIDE_NO = "no"
SIDES = (SIDE_YES, SIDE_NO)

ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"

STATUS_OPEN = "open"
STATUS_PAUSED = "paused"
STATUS_CLOSED = "closed"
STATUS_RESOLVED = "resolved"
STATUS_DELETED = "deleted"

# Profile pictures a user can pick (stored by name; "" means none picked yet).
AVATARS = ("fox", "panda", "cat", "dog", "frog", "owl", "rabbit")

VOTE_DELETE = "delete"
VOTE_KEEP = "keep"

# Event types. Over/under is stored with the same two sides: yes = over, no = under.
TYPE_YES_NO = "yes_no"
TYPE_OVER_UNDER = "over_under"
EVENT_TYPES = (TYPE_YES_NO, TYPE_OVER_UNDER)


@dataclass
class User:
    """A person who can log in and bet. Balance is in coins, 2 decimals.

    group_id, role, balance and restricted are not stored on the user: they
    come from the user's membership (see Membership) and are filled in when
    data is loaded.
    """

    id: str
    username: str
    password_hash: str
    group_id: Optional[str] = None
    role: str = ROLE_MEMBER
    balance: Decimal = Decimal("0")
    avatar: str = ""
    restricted: bool = False


@dataclass
class Group:
    """A friend group with one admin and a shared starting balance."""

    id: str
    name: str
    admin_id: str
    invite_code: str
    start_balance: Decimal = Decimal("1000")


@dataclass
class Membership:
    """A user's place in one group: their role, balance and restriction there."""

    id: str
    user_id: str
    group_id: str
    role: str = ROLE_MEMBER
    balance: Decimal = Decimal("0")
    restricted: bool = False


@dataclass
class Event:
    """Something the group bets on. Outcome is None until resolved.

    event_type is yes/no or over/under. For over/under, line is the number set
    by the creator and result_value the actual number entered when resolving.
    event_time is when the event happens; betting closes at close_time, which
    is never later. Old events without an event date use their close_time.
    """

    id: str
    group_id: str
    creator_id: str
    title: str
    close_time: datetime
    description: str = ""
    status: str = STATUS_OPEN
    outcome: Optional[str] = None
    flags: list = field(default_factory=list)
    event_type: str = TYPE_YES_NO
    line: Optional[Decimal] = None
    event_time: Optional[datetime] = None
    result_value: Optional[Decimal] = None

    def __post_init__(self) -> None:
        """Use the close time as the event date when none is given."""
        if self.event_time is None:
            self.event_time = self.close_time


@dataclass
class Bet:
    """A stake placed by a user on one side of an event."""

    id: str
    event_id: str
    user_id: str
    side: str
    stake: Decimal
    payout: Optional[Decimal] = None


@dataclass
class Poll:
    """A vote on whether a flagged event should be deleted."""

    id: str
    event_id: str
    opened_by: str
    closes_at: datetime
    votes: dict = field(default_factory=dict)
    result: Optional[str] = None


@dataclass
class TopUp:
    """Coins the admin gave to every member of a group at once."""

    id: str
    group_id: str
    amount: Decimal
    created_by: str
    created_at: datetime


@dataclass
class Repayment:
    """The moment the admin confirmed "Repaid": a new period starts."""

    id: str
    group_id: str
    created_by: str
    created_at: datetime


@dataclass
class Notification:
    """A message for one user inside the app (no email or push)."""

    id: str
    user_id: str
    group_id: Optional[str]
    text: str
    created_at: datetime
    read: bool = False

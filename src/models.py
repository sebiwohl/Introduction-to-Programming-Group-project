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

# Event types. A yes/no event has the two sides above; a names event has its
# own options (people's names, for example), and bets name one of them.
KIND_YES_NO = "yes_no"
KIND_NAMES = "names"
MIN_OPTIONS = 2
MAX_OPTIONS = 10
MAX_OPTION_LENGTH = 40

ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"

STATUS_OPEN = "open"
STATUS_PAUSED = "paused"
STATUS_CLOSED = "closed"
STATUS_RESOLVED = "resolved"
STATUS_DELETED = "deleted"

VOTE_DELETE = "delete"
VOTE_KEEP = "keep"


@dataclass
class User:
    """A person who can log in and bet. Balance is in coins, 2 decimals."""

    id: str
    username: str
    password_hash: str
    group_id: Optional[str] = None
    role: str = ROLE_MEMBER
    balance: Decimal = Decimal("0")


@dataclass
class Group:
    """A friend group with one admin and a shared starting balance."""

    id: str
    name: str
    admin_id: str
    invite_code: str
    start_balance: Decimal = Decimal("1000")


@dataclass
class Option:
    """One answer of a names event, like 'Marta'. Bets refer to it by id."""

    id: str
    name: str


@dataclass
class Event:
    """Something the group bets on. Outcome is None until resolved.

    A names event (kind KIND_NAMES) has options instead of yes and no. Its
    winners are the ids of the winning options, set when it is resolved (its
    outcome stays None). With multi_winner, more than one option can win.
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
    kind: str = KIND_YES_NO
    options: list = field(default_factory=list)
    multi_winner: bool = False
    winners: list = field(default_factory=list)


@dataclass
class Bet:
    """A stake placed by a user on one side of an event (or one option of a names event)."""

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

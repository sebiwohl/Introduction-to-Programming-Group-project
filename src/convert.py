"""Convert between the app's dataclasses and the dicts saved by storage.py.

In the app, money is a Decimal with 2 decimals (12.50) and times are
timezone-aware datetimes. In the JSON file, money is whole cents (1250) and
times are ISO 8601 strings with a timezone offset ("2026-11-20T19:00:00+00:00").
This module is the only place that translates between the two.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.models import CENT, Bet, Event, Group, Poll, User


class ConversionError(ValueError):
    """Raised when a value cannot be converted safely."""


# --- money --------------------------------------------------------------------

def money_to_cents(amount: Decimal) -> int:
    """Return an amount with at most 2 decimals as whole cents (12.50 -> 1250)."""
    if isinstance(amount, (bool, float)) or not isinstance(amount, (int, Decimal)):
        raise ConversionError(f"Money must be an int or Decimal, got {amount!r}.")
    amount = Decimal(amount)
    if not amount.is_finite() or amount != amount.quantize(CENT):
        raise ConversionError(f"Money can have at most 2 decimals, got {amount}.")
    return int(amount * 100)


def cents_to_money(cents: int) -> Decimal:
    """Return whole cents as a 2-decimal amount (1250 -> Decimal('12.50'))."""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise ConversionError(f"Cents must be a whole number, got {cents!r}.")
    return (Decimal(cents) / 100).quantize(CENT)


def _optional_cents(amount: Optional[Decimal]) -> Optional[int]:
    """Convert money to cents, keeping None as None."""
    return None if amount is None else money_to_cents(amount)


def _optional_money(cents: Optional[int]) -> Optional[Decimal]:
    """Convert cents to money, keeping None as None."""
    return None if cents is None else cents_to_money(cents)


# --- time ---------------------------------------------------------------------

def time_to_text(moment: datetime) -> str:
    """Return a timezone-aware datetime as an ISO 8601 string with offset."""
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ConversionError("Times must be timezone-aware datetimes.")
    return moment.isoformat()


def text_to_time(text: str) -> datetime:
    """Return an ISO 8601 string with a timezone offset as a datetime."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ConversionError(f"Not an ISO 8601 time: {text!r}.") from error
    if moment.utcoffset() is None:
        raise ConversionError(f"Time has no timezone offset: {text!r}.")
    return moment


# --- users and groups -------------------------------------------------------

def user_to_dict(user: User) -> dict:
    """Return a User as a dict for storage."""
    return {
        "id": user.id, "username": user.username,
        "password_hash": user.password_hash, "group_id": user.group_id,
        "role": user.role, "balance": money_to_cents(user.balance),
    }


def user_from_dict(data: dict) -> User:
    """Return a User from a stored dict."""
    return User(
        id=data["id"], username=data["username"],
        password_hash=data["password_hash"], group_id=data["group_id"],
        role=data["role"], balance=cents_to_money(data["balance"]),
    )


def group_to_dict(group: Group) -> dict:
    """Return a Group as a dict for storage."""
    return {
        "id": group.id, "name": group.name, "admin_id": group.admin_id,
        "invite_code": group.invite_code,
        "start_balance": money_to_cents(group.start_balance),
    }


def group_from_dict(data: dict) -> Group:
    """Return a Group from a stored dict."""
    return Group(
        id=data["id"], name=data["name"], admin_id=data["admin_id"],
        invite_code=data["invite_code"],
        start_balance=cents_to_money(data["start_balance"]),
    )


# --- events, bets and polls ---------------------------------------------------

def event_to_dict(event: Event) -> dict:
    """Return an Event as a dict for storage."""
    return {
        "id": event.id, "group_id": event.group_id,
        "creator_id": event.creator_id, "title": event.title,
        "description": event.description,
        "close_time": time_to_text(event.close_time),
        "status": event.status, "outcome": event.outcome,
        "flags": list(event.flags),
    }


def event_from_dict(data: dict) -> Event:
    """Return an Event from a stored dict."""
    return Event(
        id=data["id"], group_id=data["group_id"],
        creator_id=data["creator_id"], title=data["title"],
        description=data["description"],
        close_time=text_to_time(data["close_time"]),
        status=data["status"], outcome=data["outcome"],
        flags=list(data["flags"]),
    )


def bet_to_dict(bet: Bet) -> dict:
    """Return a Bet as a dict for storage."""
    return {
        "id": bet.id, "event_id": bet.event_id, "user_id": bet.user_id,
        "side": bet.side, "stake": money_to_cents(bet.stake),
        "payout": _optional_cents(bet.payout),
    }


def bet_from_dict(data: dict) -> Bet:
    """Return a Bet from a stored dict."""
    return Bet(
        id=data["id"], event_id=data["event_id"], user_id=data["user_id"],
        side=data["side"], stake=cents_to_money(data["stake"]),
        payout=_optional_money(data["payout"]),
    )


def poll_to_dict(poll: Poll) -> dict:
    """Return a Poll as a dict for storage."""
    return {
        "id": poll.id, "event_id": poll.event_id, "opened_by": poll.opened_by,
        "closes_at": time_to_text(poll.closes_at),
        "votes": dict(poll.votes), "result": poll.result,
    }


def poll_from_dict(data: dict) -> Poll:
    """Return a Poll from a stored dict."""
    return Poll(
        id=data["id"], event_id=data["event_id"], opened_by=data["opened_by"],
        closes_at=text_to_time(data["closes_at"]),
        votes=dict(data["votes"]), result=data["result"],
    )

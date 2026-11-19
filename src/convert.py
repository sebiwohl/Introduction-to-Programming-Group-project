"""Convert between the app's dataclasses and the dicts saved by storage.py.

In the app, money is a Decimal with 2 decimals (12.50) and times are
timezone-aware datetimes. In the JSON file, money is whole cents (1250) and
times are ISO 8601 strings with a timezone offset ("2026-11-20T19:00:00+00:00").
Numbers that are not money (an over/under line, the actual result) are stored
as decimal text ("2.5"), so they never pass through a float.
This module is the only place that translates between the two.

Stored users have no group, role or balance: those live in memberships. The
app's User still carries them for the user's group, filled in on loading.
"""

from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Iterable, Optional

from src.models import (
    CENT, TYPE_NAMES, Bet, Event, Group, Membership, Notification, Option, Poll, Repayment,
    TopUp, User,
)


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


# --- decimal numbers (not money) -----------------------------------------------

def number_to_text(number: Decimal) -> str:
    """Return a finite Decimal as text for storage (Decimal('2.5') -> '2.5')."""
    if isinstance(number, (bool, float)) or not isinstance(number, (int, Decimal)):
        raise ConversionError(f"Numbers must be an int or Decimal, got {number!r}.")
    number = Decimal(number)
    if not number.is_finite():
        raise ConversionError(f"Numbers must be finite, got {number}.")
    return str(number)


def text_to_number(text: str) -> Decimal:
    """Return stored decimal text as a Decimal ('2.5' -> Decimal('2.5'))."""
    try:
        number = Decimal(text) if isinstance(text, str) else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite():
        raise ConversionError(f"Not a decimal number: {text!r}.")
    return number


def _optional_text(number: Optional[Decimal]) -> Optional[str]:
    """Convert a number to text, keeping None as None."""
    return None if number is None else number_to_text(number)


def _optional_number(text: Optional[str]) -> Optional[Decimal]:
    """Convert text to a number, keeping None as None."""
    return None if text is None else text_to_number(text)


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
    """Return a User as a dict for storage (group, role and balance are not stored here).

    The avatar is left out until the user picks one.
    """
    data = {"id": user.id, "username": user.username, "password_hash": user.password_hash}
    if user.avatar:
        data["avatar"] = user.avatar
    return data


def find_membership(memberships: Iterable[dict], user_id: str) -> Optional[dict]:
    """Return the user's first stored membership dict, or None if they have no group."""
    return next((m for m in memberships if m["user_id"] == user_id), None)


def user_from_dict(data: dict, membership: Optional[dict] = None) -> User:
    """Return a User from a stored dict, with group, role and balance from the membership."""
    user = User(id=data["id"], username=data["username"], password_hash=data["password_hash"],
                avatar=data.get("avatar", ""))
    if membership is not None:
        user.group_id = membership["group_id"]
        user.role = membership["role"]
        user.balance = cents_to_money(membership["balance"])
    return user


def membership_to_dict(membership: Membership) -> dict:
    """Return a Membership as a dict for storage."""
    return {
        "id": membership.id, "user_id": membership.user_id,
        "group_id": membership.group_id, "role": membership.role,
        "balance": money_to_cents(membership.balance),
        "restricted": membership.restricted,
    }


def membership_from_dict(data: dict) -> Membership:
    """Return a Membership from a stored dict."""
    return Membership(
        id=data["id"], user_id=data["user_id"], group_id=data["group_id"],
        role=data["role"], balance=cents_to_money(data["balance"]),
        restricted=data["restricted"],
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
    """Return an Event as a dict for storage.

    The names fields (options, multi_winner, winners) are written only for
    names events, so other events are stored exactly as before they existed.
    """
    data = {
        "id": event.id, "group_id": event.group_id,
        "creator_id": event.creator_id, "title": event.title,
        "description": event.description,
        "close_time": time_to_text(event.close_time),
        "status": event.status, "outcome": event.outcome,
        "flags": list(event.flags), "type": event.event_type,
        "line": _optional_text(event.line),
        "event_time": time_to_text(event.event_time),
        "result_value": _optional_text(event.result_value),
    }
    if event.event_type == TYPE_NAMES:
        data.update({
            "options": [{"id": option.id, "name": option.name} for option in event.options],
            "multi_winner": event.multi_winner, "winners": list(event.winners),
        })
    return data


def event_from_dict(data: dict) -> Event:
    """Return an Event from a stored dict; the names fields default to empty."""
    return Event(
        id=data["id"], group_id=data["group_id"],
        creator_id=data["creator_id"], title=data["title"],
        description=data["description"],
        close_time=text_to_time(data["close_time"]),
        status=data["status"], outcome=data["outcome"],
        flags=list(data["flags"]), event_type=data["type"],
        line=_optional_number(data["line"]),
        event_time=text_to_time(data["event_time"]),
        result_value=_optional_number(data["result_value"]),
        options=[Option(id=o["id"], name=o["name"]) for o in data.get("options", [])],
        multi_winner=data.get("multi_winner", False),
        winners=list(data.get("winners", [])),
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


# --- top-ups, repayments and notifications ------------------------------------

def topup_to_dict(topup: TopUp) -> dict:
    """Return a TopUp as a dict for storage."""
    return {
        "id": topup.id, "group_id": topup.group_id,
        "amount": money_to_cents(topup.amount), "created_by": topup.created_by,
        "created_at": time_to_text(topup.created_at),
    }


def topup_from_dict(data: dict) -> TopUp:
    """Return a TopUp from a stored dict."""
    return TopUp(
        id=data["id"], group_id=data["group_id"], amount=cents_to_money(data["amount"]),
        created_by=data["created_by"], created_at=text_to_time(data["created_at"]),
    )


def repayment_to_dict(repayment: Repayment) -> dict:
    """Return a Repayment as a dict for storage."""
    return {
        "id": repayment.id, "group_id": repayment.group_id,
        "created_by": repayment.created_by,
        "created_at": time_to_text(repayment.created_at),
    }


def repayment_from_dict(data: dict) -> Repayment:
    """Return a Repayment from a stored dict."""
    return Repayment(
        id=data["id"], group_id=data["group_id"], created_by=data["created_by"],
        created_at=text_to_time(data["created_at"]),
    )


def notification_to_dict(note: Notification) -> dict:
    """Return a Notification as a dict for storage."""
    return {
        "id": note.id, "user_id": note.user_id, "group_id": note.group_id,
        "text": note.text, "created_at": time_to_text(note.created_at),
        "read": note.read,
    }


def notification_from_dict(data: dict) -> Notification:
    """Return a Notification from a stored dict."""
    return Notification(
        id=data["id"], user_id=data["user_id"], group_id=data["group_id"],
        text=data["text"], created_at=text_to_time(data["created_at"]),
        read=data["read"],
    )


def membership_dicts(users: Iterable[User]) -> list:
    """Return stored memberships for users that carry a group (seed data and tests)."""
    from src.migration import membership_id

    return [
        membership_to_dict(Membership(
            id=membership_id(user.id, user.group_id), user_id=user.id,
            group_id=user.group_id, role=user.role, balance=user.balance,
        ))
        for user in users if user.group_id
    ]

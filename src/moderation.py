"""Deleting events, flags and polls (requirements 2.1, 3.2 and 3.4).

- The creator or the admin can delete an event more than 3 hours before
  betting closes; every stake is refunded.
- Members flag fishy open events, one flag each. Counting only unrestricted
  members: at least 25% of them (and at least 2) pauses the event and opens a
  poll; more than 50% (and at least 2) deletes it at once.
- The admin can open a poll directly. A poll runs 1 hour, or until halfway to
  the close if that is sooner. One vote each. More than 50% "delete" among the
  votes cast deletes the event; a tie or no votes keeps it, clears the flags
  and reopens betting.
- There is no timer: end_due_polls() runs when a page loads (service.refresh).

All functions work on the stored dicts inside storage.transaction(), like
members.py. Permissions come from auth.require_permission().
"""

import math
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from src import auth, storage
from src.auth import AuthError
from src.convert import event_from_dict, poll_to_dict, text_to_time
from src.models import (
    STATUS_DELETED, STATUS_OPEN, STATUS_PAUSED, VOTE_DELETE, VOTE_KEEP, Poll,
)

DELETE_DEADLINE = timedelta(hours=3)
POLL_LENGTH = timedelta(hours=1)
POLL_SHARE = 0.25
MIN_FLAGS = 2
AUTO_OPENER = "auto"
RESULT_DELETED = "deleted"
RESULT_KEPT = "kept"

# What flag_event() did.
FLAGGED = "flagged"
POLL_OPENED = "poll_opened"
DELETED = "deleted"


# --- helpers on the stored data -------------------------------------------------

def _find_event(data: dict, event_id: str) -> dict:
    """Return the stored event, or raise AuthError."""
    event = next((e for e in data["events"] if e["id"] == event_id), None)
    if event is None:
        raise AuthError("Event not found.")
    return event


def _check(data: dict, user_id: str, event: dict, action: str) -> None:
    """Raise AuthError unless the user may do `action` on the event.

    Someone outside the event's group gets "Event not found", so ids reveal nothing.
    """
    try:
        user = auth.user_in_group(data, user_id, event["group_id"])
    except AuthError:
        raise AuthError("Event not found.") from None
    auth.require_permission(user, action, event_from_dict(event))


def _running_poll(data: dict, event_id: str) -> Optional[dict]:
    """Return the event's poll that has not ended yet, or None."""
    return next((p for p in data["polls"]
                 if p["event_id"] == event_id and p["result"] is None), None)


def _unrestricted_members(data: dict, group_id: str) -> set:
    """Return the ids of the group's members who are not restricted."""
    return {m["user_id"] for m in data["memberships"]
            if m["group_id"] == group_id and not m["restricted"]}


def _delete_and_refund(data: dict, event: dict) -> None:
    """Mark the event deleted and give every unsettled stake back to its member.

    Works for every event type, because it only uses each bet's stake.
    """
    event["status"] = STATUS_DELETED
    balances = {m["user_id"]: m for m in data["memberships"]
                if m["group_id"] == event["group_id"]}
    for bet in data["bets"]:
        if bet["event_id"] == event["id"] and bet["payout"] is None:
            bet["payout"] = bet["stake"]
            if bet["user_id"] in balances:
                balances[bet["user_id"]]["balance"] += bet["stake"]


def _poll_end(now: datetime, close_time: datetime) -> datetime:
    """Return when a poll opened now ends: in 1 hour, or halfway to the close if sooner."""
    return min(now + POLL_LENGTH, now + (close_time - now) / 2)


def _open_poll(data: dict, event: dict, opened_by: str, now: datetime) -> Poll:
    """Pause the event and store a new poll on it; return the poll."""
    close_time = text_to_time(event["close_time"])
    poll = Poll(id=uuid.uuid4().hex, event_id=event["id"], opened_by=opened_by,
                closes_at=_poll_end(now, close_time))
    event["status"] = STATUS_PAUSED
    data["polls"].append(poll_to_dict(poll))
    return poll


def _require_open(event: dict, now: datetime, what: str) -> None:
    """Raise AuthError unless the event is open and betting has not closed yet."""
    if event["status"] != STATUS_OPEN or now >= text_to_time(event["close_time"]):
        raise AuthError(f"Only open events can {what}.")


# --- deleting an event ----------------------------------------------------------

def delete_event(user_id: str, event_id: str, now: datetime, path=None) -> None:
    """Delete an open or paused event and refund every stake.

    Only the creator or the admin, and only more than 3 hours before betting
    closes; after that, only a poll can remove it. A running poll ends as deleted.
    """
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, user_id, event, auth.DELETE_EVENT)
        if event["status"] not in (STATUS_OPEN, STATUS_PAUSED):
            raise AuthError(f"An event that is {event['status']} cannot be deleted.")
        if now >= text_to_time(event["close_time"]) - DELETE_DEADLINE:
            raise AuthError("Events can only be deleted more than 3 hours before betting "
                            "closes. After that, flag it so the group can vote.")
        poll = _running_poll(data, event_id)
        if poll is not None:
            poll["result"] = RESULT_DELETED
        _delete_and_refund(data, event)


# --- flags ----------------------------------------------------------------------

def poll_threshold(members: int) -> int:
    """Return how many flags open a poll: 25% of the members, at least 2."""
    return max(MIN_FLAGS, math.ceil(members * POLL_SHARE))


def flag_event(user_id: str, event_id: str, now: datetime, path=None) -> str:
    """Flag an open event; return FLAGGED, POLL_OPENED or DELETED.

    Only flags of unrestricted members count, and only they count as members.
    """
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, user_id, event, auth.FLAG_EVENT)
        _require_open(event, now, "be flagged")
        if user_id in event["flags"]:
            raise AuthError("You have already flagged this event.")
        event["flags"].append(user_id)
        members = _unrestricted_members(data, event["group_id"])
        flags = len(members.intersection(event["flags"]))
        if flags >= MIN_FLAGS and flags * 2 > len(members):
            _delete_and_refund(data, event)
            return DELETED
        if flags >= poll_threshold(len(members)):
            _open_poll(data, event, AUTO_OPENER, now)
            return POLL_OPENED
        return FLAGGED


# --- polls ----------------------------------------------------------------------

def open_poll(admin_id: str, event_id: str, now: datetime, path=None) -> Poll:
    """Let the admin open a poll on an open event directly, without flags."""
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, admin_id, event, auth.OPEN_POLL)
        _require_open(event, now, "get a poll")
        return _open_poll(data, event, admin_id, now)


def vote(user_id: str, event_id: str, choice: str, now: datetime, path=None) -> None:
    """Cast the user's one vote, "delete" or "keep", in the event's running poll."""
    if choice not in (VOTE_DELETE, VOTE_KEEP):
        raise AuthError('Vote "delete" or "keep".')
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, user_id, event, auth.VOTE_POLL)
        poll = _running_poll(data, event_id)
        if poll is None or now >= text_to_time(poll["closes_at"]):
            raise AuthError("There is no running poll on this event.")
        if user_id in poll["votes"]:
            raise AuthError("You have already voted in this poll.")
        poll["votes"][user_id] = choice


def polls_due(polls: List[Poll], now: datetime) -> bool:
    """Return True if any running poll has reached its end time."""
    return any(p.result is None and now >= p.closes_at for p in polls)


def end_due_polls(data: dict, now: datetime) -> int:
    """End every running poll past its end time (inside a transaction); return how many.

    More than 50% "delete" among the votes cast deletes the event with refunds.
    Otherwise (a tie or no votes too) the event reopens and its flags are cleared.
    """
    ended = 0
    for poll in data["polls"]:
        if poll["result"] is not None or now < text_to_time(poll["closes_at"]):
            continue
        event = _find_event(data, poll["event_id"])
        deletes = sum(1 for choice in poll["votes"].values() if choice == VOTE_DELETE)
        if deletes * 2 > len(poll["votes"]):
            poll["result"] = RESULT_DELETED
            _delete_and_refund(data, event)
        else:
            poll["result"] = RESULT_KEPT
            event["status"] = STATUS_OPEN
            event["flags"] = []
        ended += 1
    return ended

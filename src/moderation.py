"""Deleting events, flags and polls (requirements 2.1, 3.2 and 3.4).

- The creator or the admin can delete an event more than 3 hours before
  betting closes; every stake is refunded.
- Members flag fishy open events, one flag each. Counting only unrestricted
  members: at least 25% of them (and at least 2) pauses the event and opens a
  poll; more than 50% (and at least 2) deletes it at once.
- The admin can open a poll directly. A poll runs 1 hour, or until halfway to
  the close if that is sooner. One vote each. Every unrestricted member who
  flagged the event starts with a "delete" vote; they may switch it to "keep"
  once. More than 50% "delete" among the votes cast deletes the event; a tie or
  no votes keeps it, clears the flags and reopens betting.
- There is no timer: end_due_polls() runs when a page loads (service.refresh).
- Every flag, poll and deletion goes into the group's activity log (the bell
  of every member except whoever did it).

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
from src.notifications import notify_group, quote
from src.models import (
    MAX_FLAG_NOTE_LENGTH, STATUS_DELETED, STATUS_OPEN, STATUS_PAUSED, VOTE_DELETE, VOTE_KEEP, Poll,
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

    Works for every event type, because it only uses each bet's stake. It credits
    the membership in the event's own group; betting._refund_all/_settle work on
    User objects, which carry only one group's balance, so they are not used here.
    """
    event["status"] = STATUS_DELETED
    balances = {m["user_id"]: m for m in data["memberships"]
                if m["group_id"] == event["group_id"]}
    for bet in data["bets"]:
        if bet["event_id"] == event["id"] and bet["payout"] is None:
            bet["payout"] = bet["stake"]
            if bet["user_id"] in balances:
                balances[bet["user_id"]]["balance"] += bet["stake"]


def _name(data: dict, user_id: str) -> str:
    """Return a user's name."""
    return next(u["username"] for u in data["users"] if u["id"] == user_id)


def _log(data: dict, event: dict, text: str, now: datetime, skip=()) -> None:
    """Add an entry to the activity log of the event's group."""
    notify_group(data, event["group_id"], text, now, skip=skip)


def _poll_end(now: datetime, close_time: datetime) -> datetime:
    """Return when a poll opened now ends: in 1 hour, or halfway to the close if sooner."""
    return min(now + POLL_LENGTH, now + (close_time - now) / 2)


def _flag_votes(data: dict, event: dict) -> dict:
    """Return a "delete" vote for every unrestricted member who flagged the event."""
    members = _unrestricted_members(data, event["group_id"])
    return {user_id: VOTE_DELETE for user_id in event["flags"] if user_id in members}


def _open_poll(data: dict, event: dict, opened_by: str, now: datetime) -> Poll:
    """Pause the event and store a new poll on it; return the poll.

    The members who flagged the event start with a "delete" vote.
    """
    close_time = text_to_time(event["close_time"])
    poll = Poll(id=uuid.uuid4().hex, event_id=event["id"], opened_by=opened_by,
                closes_at=_poll_end(now, close_time), votes=_flag_votes(data, event))
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
        _log(data, event, f"{_name(data, user_id)} deleted {quote(event['title'])}. "
             "Every stake was refunded.", now, skip=[user_id])


# --- flags ----------------------------------------------------------------------

def poll_threshold(members: int) -> int:
    """Return how many flags open a poll: 25% of the members, at least 2."""
    return max(MIN_FLAGS, math.ceil(members * POLL_SHARE))


def clean_flag_note(note: str) -> str:
    """Return the flag note trimmed, with runs of spaces and line breaks made one space.

    The note is optional ("" is fine); raise AuthError if it is longer than
    MAX_FLAG_NOTE_LENGTH characters.
    """
    cleaned = " ".join(str(note or "").split())
    if len(cleaned) > MAX_FLAG_NOTE_LENGTH:
        raise AuthError(f"The note can be at most {MAX_FLAG_NOTE_LENGTH} characters.")
    return cleaned


def flag_event(user_id: str, event_id: str, now: datetime, path=None, note: str = "") -> str:
    """Flag an open event, with an optional note saying why; return FLAGGED, POLL_OPENED or DELETED.

    Only flags of unrestricted members count, and only they count as members.
    The note is shown to the group on the event card and in the activity log.
    """
    note = clean_flag_note(note)
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, user_id, event, auth.FLAG_EVENT)
        _require_open(event, now, "be flagged")
        if user_id in event["flags"]:
            raise AuthError("You have already flagged this event.")
        event["flags"].append(user_id)
        if note:
            event.setdefault("flag_notes", {})[user_id] = note
        members = _unrestricted_members(data, event["group_id"])
        flags = len(members.intersection(event["flags"]))
        title = quote(event["title"])
        if flags >= MIN_FLAGS and flags * 2 > len(members):
            _delete_and_refund(data, event)
            _log(data, event, f"{title} was deleted: more than half of the members flagged it. "
                 "Every stake was refunded.", now, skip=[user_id])
            return DELETED
        if flags >= poll_threshold(len(members)):
            _open_poll(data, event, AUTO_OPENER, now)
            _log(data, event, f"Enough flags on {title}: betting is paused. "
                 "Vote in the poll on the Feed.", now)
            return POLL_OPENED
        reason = f": {quote(note)}" if note else "."  # shortened, so the log entry stays short
        _log(data, event, f"{_name(data, user_id)} flagged {title}{reason}", now, skip=[user_id])
        return FLAGGED


# --- polls ----------------------------------------------------------------------

def open_poll(admin_id: str, event_id: str, now: datetime, path=None) -> Poll:
    """Let the admin open a poll on an open event directly, without flags."""
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, admin_id, event, auth.OPEN_POLL)
        _require_open(event, now, "get a poll")
        poll = _open_poll(data, event, admin_id, now)
        _log(data, event, f"{_name(data, admin_id)} opened a poll on {quote(event['title'])}: "
             "betting is paused. Vote on the Feed.", now, skip=[admin_id])
        return poll


def vote_from_flag(votes: dict, flags: List[str], user_id: str) -> bool:
    """Return True if the user's vote is still the "delete" vote their flag gave them.

    Such a vote may be switched to "keep" once; after that it is final.
    """
    return user_id in flags and votes.get(user_id) == VOTE_DELETE


def vote(user_id: str, event_id: str, choice: str, now: datetime, path=None) -> None:
    """Cast the user's one vote, "delete" or "keep", in the event's running poll.

    A member whose flag gave them a "delete" vote may switch it to "keep" once.
    """
    if choice not in (VOTE_DELETE, VOTE_KEEP):
        raise AuthError('Vote "delete" or "keep".')
    with storage.transaction(path) as data:
        event = _find_event(data, event_id)
        _check(data, user_id, event, auth.VOTE_POLL)
        poll = _running_poll(data, event_id)
        if poll is None or now >= text_to_time(poll["closes_at"]):
            raise AuthError("There is no running poll on this event.")
        switching = choice == VOTE_KEEP and vote_from_flag(poll["votes"], event["flags"], user_id)
        if user_id in poll["votes"] and not switching:
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
        votes = f"{deletes} of {len(poll['votes'])} vote{'' if len(poll['votes']) == 1 else 's'}"
        if deletes * 2 > len(poll["votes"]):
            poll["result"] = RESULT_DELETED
            _delete_and_refund(data, event)
            _log(data, event, f"The poll on {quote(event['title'])} ended: deleted ({votes} for "
                 "delete). Every stake was refunded.", now)
        else:
            poll["result"] = RESULT_KEPT
            event["status"] = STATUS_OPEN
            event["flags"] = []
            event.pop("flag_notes", None)  # the notes go with the flags
            _log(data, event, f"The poll on {quote(event['title'])} ended: kept ({votes} for "
                 "delete). Betting is open again.", now)
        ended += 1
    return ended

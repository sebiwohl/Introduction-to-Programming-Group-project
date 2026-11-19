"""In-app notifications: add, list and mark as read (requirements 2.2 and 2.4).

There is no email or push: a notification is a stored message that the user
sees in the app. Other modules call add_notification() (one user) or
notify_group() (the group's activity log: every member except whoever did it)
inside their own storage.transaction(), so the notification is saved together
with the change that caused it. Users can only see and mark their own
notifications.
"""

import uuid
from datetime import datetime
from typing import Iterable, List, Optional

from src import storage
from src.betting import BettingError
from src.convert import notification_from_dict, notification_to_dict
from src.models import Notification

MAX_TEXT_LENGTH = 200
MAX_TITLE_IN_TEXT = 60  # event titles are shortened so the whole text stays short
ACTIVITY_PREFIX = "log:"  # ids of the group activity log entries (notify_group)


class NotificationError(BettingError):
    """Raised when a notification action breaks a rule. Nothing is changed."""


def _clean_text(text: str) -> str:
    """Return the trimmed text, or raise NotificationError if empty or too long."""
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise NotificationError("A notification needs a text.")
    if len(text) > MAX_TEXT_LENGTH:
        raise NotificationError(f"A notification can be at most {MAX_TEXT_LENGTH} characters.")
    return text


def add_notification(
    data: dict, user_id: str, group_id: Optional[str], text: str, now: datetime,
    id_prefix: str = "",
) -> Notification:
    """Add a notification for one user to `data` (inside a transaction) and return it."""
    if not any(user["id"] == user_id for user in data["users"]):
        raise NotificationError("Unknown user.")
    if group_id is not None and not any(group["id"] == group_id for group in data["groups"]):
        raise NotificationError("Unknown group.")
    note = Notification(id=id_prefix + uuid.uuid4().hex, user_id=user_id, group_id=group_id,
                        text=_clean_text(text), created_at=now)
    data["notifications"].append(notification_to_dict(note))
    return note


def user_notifications(data: dict, user_id: str) -> List[Notification]:
    """Return the user's notifications from loaded data, newest first."""
    notes = [notification_from_dict(note) for note in data["notifications"]
             if note["user_id"] == user_id]
    return sorted(notes, key=lambda note: note.created_at, reverse=True)


def quote(title: str) -> str:
    """Return an event title in quotes, shortened so it fits in a notification."""
    if len(title) > MAX_TITLE_IN_TEXT:
        title = title[:MAX_TITLE_IN_TEXT - 1].rstrip() + "…"
    return f"“{title}”"


def notify_group(data: dict, group_id: str, text: str, now: datetime,
                 skip: Iterable[str] = ()) -> int:
    """Add the same notification for every member of the group except `skip`; return how many.

    Used for the group's activity log (top-ups, members, flags, polls). Pass
    whoever did the action in `skip`: they already know.
    """
    skipped = set(skip)
    members = [m["user_id"] for m in data["memberships"]
               if m["group_id"] == group_id and m["user_id"] not in skipped]
    for user_id in members:
        add_notification(data, user_id, group_id, text, now, id_prefix=ACTIVITY_PREFIX)
    return len(members)


def is_activity(note: Notification) -> bool:
    """Return True for a group activity log entry (not a personal message)."""
    return note.id.startswith(ACTIVITY_PREFIX)


def notifications_for(user_id: str, path=None) -> List[Notification]:
    """Return the user's notifications, newest first."""
    return user_notifications(storage.load_data(path), user_id)


def unread_count(notes: Iterable[Notification]) -> int:
    """Return how many of the notifications are unread."""
    return sum(1 for note in notes if not note.read)


def mark_read(user_id: str, note_id: str, path=None) -> None:
    """Mark one of the user's own notifications as read.

    Someone else's notification is reported as unknown, so its id reveals nothing.
    """
    with storage.transaction(path) as data:
        for note in data["notifications"]:
            if note["id"] == note_id and note["user_id"] == user_id:
                note["read"] = True
                return
        raise NotificationError("Unknown notification.")


def mark_all_read(user_id: str, path=None, skip_prefix: Optional[str] = None) -> int:
    """Mark all of the user's notifications as read; return how many changed.

    Notifications whose id starts with skip_prefix stay unread (the bell leaves
    unseen wins alone, so their confetti still plays on the Feed).
    """
    with storage.transaction(path) as data:
        unread = [note for note in data["notifications"]
                  if note["user_id"] == user_id and not note["read"]
                  and not (skip_prefix and note["id"].startswith(skip_prefix))]
        for note in unread:
            note["read"] = True
    return len(unread)

"""Storage module: the only place that reads or writes the stored data.

Two places can hold the data, with the same format and the same rules:

* A JSON file (``data/data.json`` by default). Every save is validated first,
  written to a temporary file and moved into place atomically. The previous
  version is kept as a backup (the newest ``MAX_BACKUPS`` are kept), so a
  damaged file can be restored at start-up.
* A PostgreSQL database such as Supabase, used when the ``DATABASE_URL``
  environment variable is set and no file path is passed. The whole data dict
  is one JSON row in ``app_data``; backups are rows in ``app_data_backups``.
  Needed on hosts that erase local files on restart (Streamlit Community Cloud).

Data saved in an older format (``schema_version`` 1) is upgraded on loading by
``src/migration.py`` and written in the current format at the next save.

An explicit ``path`` argument always means the file, so tests never touch the
database by accident. No Streamlit; psycopg is imported only in database mode.
"""

import contextlib
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
import tempfile
from decimal import Decimal, InvalidOperation
from typing import Any

from src.migration import upgrade
from src.models import EVENT_TYPES, MAX_OPTION_LENGTH, MAX_OPTIONS, MIN_OPTIONS, TYPE_NAMES

SCHEMA_VERSION = 2
MAX_BACKUPS = 20
DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "data.json"
SECTIONS = (
    "users", "memberships", "groups", "events", "bets", "polls",
    "topups", "repayments", "notifications",
)

# On Windows, os.replace fails with PermissionError while another thread or process
# (or a virus scanner) has the target open, so the replace is retried briefly there.
RETRY_REPLACE = os.name == "nt"
REPLACE_ATTEMPTS = 20
REPLACE_DELAY_SECONDS = 0.05

Data = dict[str, Any]
PathArg = str | os.PathLike[str] | None
Predicate = Callable[[Any, dict[str, set[str]]], bool]

DATABASE_URL_ENV = "DATABASE_URL"
DB_CONNECT_TIMEOUT_SECONDS = 10

_LOCK = threading.Lock()
_STATE = threading.local()
_READY_DATABASES: set[str] = set()


class StorageError(Exception):
    """Raised when data is invalid, a file cannot be read or written, or recovery fails."""


# ---------------------------------------------------------------------------
# Field rules: each predicate says whether a value is acceptable. ``ids`` maps
# a section name to the set of record ids found in it (for reference checks).
# ---------------------------------------------------------------------------

def _is_text(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a string that is not empty or only whitespace."""
    return isinstance(value, str) and value.strip() != ""


def _is_string(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for any string, including an empty one."""
    return isinstance(value, str)


def _is_iso_time(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for an ISO 8601 datetime string with a timezone offset."""
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return False
    return moment.utcoffset() is not None


def _is_int_min(minimum: int, value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a real int (not bool, not float) that is at least ``minimum``."""
    return type(value) is int and value >= minimum


def _is_bool(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for True or False (not 0 or 1)."""
    return isinstance(value, bool)


def _is_decimal_text(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for text holding a finite decimal number, such as '2.5'."""
    if not isinstance(value, str):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _is_choice(options: tuple[str, ...], value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True if the value is a string equal to one of ``options``."""
    return isinstance(value, str) and value in options


def _is_ref(section: str, value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True if the value is the id of an existing record in ``section``."""
    return isinstance(value, str) and value in ids[section]


def _is_ref_list(section: str, value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a list of unique ids that all exist in ``section``."""
    if not isinstance(value, list):
        return False
    if not all(_is_ref(section, item, ids) for item in value):
        return False
    return len(set(value)) == len(value)


def _is_votes(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a mapping of existing user ids to 'delete' or 'keep'."""
    if not isinstance(value, dict):
        return False
    return all(
        _is_ref("users", user_id, ids) and _is_choice(("delete", "keep"), vote, ids)
        for user_id, vote in value.items()
    )


def _is_opener(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for 'auto' or the id of an existing user."""
    return value == "auto" or _is_ref("users", value, ids)


def _or_null(predicate: Predicate, value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for None or for a value the predicate accepts."""
    return value is None or predicate(value, ids)


def _is_text_list(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a list of unique non-empty strings (it may be empty)."""
    return (isinstance(value, list) and all(_is_text(item, ids) for item in value)
            and len(set(value)) == len(value))


def _is_options(value: Any, ids: dict[str, set[str]]) -> bool:
    """Return True for a names event's options.

    MIN_OPTIONS to MAX_OPTIONS objects, each with exactly an id and a name;
    ids are unique, names are trimmed, not empty, at most MAX_OPTION_LENGTH
    characters and unique ignoring case.
    """
    if not isinstance(value, list) or not MIN_OPTIONS <= len(value) <= MAX_OPTIONS:
        return False
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for option in value:
        if not isinstance(option, dict) or set(option) != {"id", "name"}:
            return False
        option_id, name = option["id"], option["name"]
        if not _is_text(option_id, ids) or not isinstance(name, str):
            return False
        if not name or name != name.strip() or len(name) > MAX_OPTION_LENGTH:
            return False
        if option_id in seen_ids or name.casefold() in seen_names:
            return False
        seen_ids.add(option_id)
        seen_names.add(name.casefold())
    return True


def _nullable(predicate: Predicate) -> Predicate:
    """Wrap a predicate so that None is accepted as well."""
    return partial(_or_null, predicate)


Rule = tuple[Predicate, str]

RULES: dict[str, dict[str, Rule]] = {
    "users": {
        "id": (_is_text, "a non-empty string"),
        "username": (_is_text, "a non-empty string"),
        "password_hash": (_is_text, "a non-empty string"),
        "avatar": (
            partial(_is_choice, ("fox", "panda", "cat", "dog", "frog", "owl", "rabbit")),
            "fox, panda, cat, dog, frog, owl or rabbit",
        ),
    },
    "memberships": {
        "id": (_is_text, "a non-empty string"),
        "user_id": (partial(_is_ref, "users"), "an existing user id"),
        "group_id": (partial(_is_ref, "groups"), "an existing group id"),
        "role": (partial(_is_choice, ("member", "admin")), "member or admin"),
        "balance": (partial(_is_int_min, 0), "a whole number >= 0"),
        "restricted": (_is_bool, "true or false"),
    },
    "groups": {
        "id": (_is_text, "a non-empty string"),
        "name": (_is_text, "a non-empty string"),
        "admin_id": (partial(_is_ref, "users"), "an existing user id"),
        "invite_code": (_is_text, "a non-empty string"),
        "start_balance": (partial(_is_int_min, 1), "a whole number > 0"),
    },
    "events": {
        "id": (_is_text, "a non-empty string"),
        "group_id": (partial(_is_ref, "groups"), "an existing group id"),
        "creator_id": (partial(_is_ref, "users"), "an existing user id"),
        "title": (_is_text, "a non-empty string"),
        "description": (_is_string, "a string"),
        "close_time": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
        "status": (
            partial(_is_choice, ("open", "paused", "closed", "resolved", "deleted")),
            "open, paused, closed, resolved or deleted",
        ),
        "outcome": (_nullable(partial(_is_choice, ("yes", "no"))), "yes, no or null"),
        "flags": (partial(_is_ref_list, "users"), "a list of unique existing user ids"),
        "type": (partial(_is_choice, EVENT_TYPES), ", ".join(EVENT_TYPES[:-1]) + f" or {EVENT_TYPES[-1]}"),
        "line": (_nullable(_is_decimal_text), "a decimal number as text, or null"),
        "event_time": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
        "result_value": (_nullable(_is_decimal_text), "a decimal number as text, or null"),
        # names events only (see OPTIONAL and _check_names_fields)
        "options": (_is_options, f"{MIN_OPTIONS} to {MAX_OPTIONS} options, each with a unique id "
                    f"and a unique, trimmed name of at most {MAX_OPTION_LENGTH} characters"),
        "multi_winner": (_is_bool, "true or false"),
        "winners": (_is_text_list, "a list of unique option ids"),
    },
    "bets": {
        "id": (_is_text, "a non-empty string"),
        "event_id": (partial(_is_ref, "events"), "an existing event id"),
        "user_id": (partial(_is_ref, "users"), "an existing user id"),
        "side": (_is_text, "yes, no or an option id"),  # checked against the event in _check_bet_sides
        "stake": (partial(_is_int_min, 1), "a whole number > 0"),
        "payout": (_nullable(partial(_is_int_min, 0)), "a whole number >= 0 or null"),
    },
    "polls": {
        "id": (_is_text, "a non-empty string"),
        "event_id": (partial(_is_ref, "events"), "an existing event id"),
        "opened_by": (_is_opener, "'auto' or an existing user id"),
        "closes_at": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
        "votes": (_is_votes, "a mapping of existing user ids to delete or keep"),
        "result": (_nullable(partial(_is_choice, ("deleted", "kept"))), "deleted, kept or null"),
    },
    "topups": {
        "id": (_is_text, "a non-empty string"),
        "group_id": (partial(_is_ref, "groups"), "an existing group id"),
        "amount": (partial(_is_int_min, 1), "a whole number > 0"),
        "created_by": (partial(_is_ref, "users"), "an existing user id"),
        "created_at": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
    },
    "repayments": {
        "id": (_is_text, "a non-empty string"),
        "group_id": (partial(_is_ref, "groups"), "an existing group id"),
        "created_by": (partial(_is_ref, "users"), "an existing user id"),
        "created_at": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
    },
    "notifications": {
        "id": (_is_text, "a non-empty string"),
        "user_id": (partial(_is_ref, "users"), "an existing user id"),
        "group_id": (_nullable(partial(_is_ref, "groups")), "an existing group id or null"),
        "text": (_is_text, "a non-empty string"),
        "created_at": (_is_iso_time, "an ISO 8601 date and time with a timezone offset"),
        "read": (_is_bool, "true or false"),
    },
}


# Fields a record may leave out (added after data was first saved). The names
# fields exist only on names events: other events are stored without them.
NAMES_FIELDS = ("options", "multi_winner", "winners")
OPTIONAL: dict[str, set[str]] = {"users": {"avatar"}, "events": set(NAMES_FIELDS)}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def empty_data() -> Data:
    """Return a fresh, empty data structure (new lists on every call)."""
    data: Data = {"schema_version": SCHEMA_VERSION}
    for section in SECTIONS:
        data[section] = []
    return data


def _label(section: str, record: Any, index: int) -> str:
    """Return a name like 'bets[b12]' (or 'bets[3]' if the record has no usable id)."""
    if isinstance(record, dict) and isinstance(record.get("id"), str) and record["id"]:
        return f"{section}[{record['id']}]"
    return f"{section}[{index}]"


def _check_top_level(data: Any) -> None:
    """Check the top-level keys, the schema version and that each section is a list."""
    if not isinstance(data, dict):
        raise StorageError(f"data must be an object, got {type(data).__name__}")
    expected = {"schema_version", *SECTIONS}
    for key in sorted(expected - set(data)):
        raise StorageError(f"missing top-level key '{key}'")
    for key in sorted(map(str, set(data) - expected)):
        raise StorageError(f"unknown top-level key '{key}'")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise StorageError(
            f"schema_version must be {SCHEMA_VERSION}, got {data['schema_version']!r}"
        )
    for section in SECTIONS:
        if not isinstance(data[section], list):
            raise StorageError(f"{section} must be a list, got {type(data[section]).__name__}")


def _collect_ids(data: Data) -> dict[str, set[str]]:
    """Check that every record is an object with a unique string id; return the id sets."""
    ids: dict[str, set[str]] = {}
    for section in SECTIONS:
        seen: set[str] = set()
        for index, record in enumerate(data[section]):
            label = _label(section, record, index)
            if not isinstance(record, dict):
                raise StorageError(f"{label} must be an object, got {type(record).__name__}")
            record_id = record.get("id")
            if not _is_text(record_id, {}):
                raise StorageError(f"{label}.id must be a non-empty string, got {record_id!r}")
            if record_id in seen:
                raise StorageError(f"{label}.id must be unique, got {record_id!r}")
            seen.add(record_id)
        ids[section] = seen
    return ids


def _check_record(section: str, record: Data, index: int, ids: dict[str, set[str]]) -> None:
    """Check that a record has exactly the allowed fields and that each value is valid.

    Fields in OPTIONAL may be missing; when present they are checked too.
    """
    label = _label(section, record, index)
    rules = RULES[section]
    for field in sorted(rules.keys() - record.keys() - OPTIONAL.get(section, set())):
        raise StorageError(f"{label}.{field} is missing")
    for field in sorted(map(str, record.keys() - rules.keys())):
        raise StorageError(f"{label}.{field} is not an allowed field")
    for field, (predicate, description) in rules.items():
        if field not in record:
            continue
        if not predicate(record[field], ids):
            raise StorageError(f"{label}.{field} must be {description}, got {record[field]!r}")


def _username_key(username: str) -> str:
    """Return the form used to compare usernames: trimmed and case-folded."""
    return username.strip().casefold()


def _check_usernames(data: Data) -> None:
    """Check that no two usernames match, ignoring case and surrounding spaces."""
    seen: dict[str, str] = {}
    for user in data["users"]:
        key = _username_key(user["username"])
        if key in seen:
            raise StorageError(
                f"users[{user['id']}].username must be unique (ignoring case and spaces), "
                f"got {user['username']!r}, which clashes with users[{seen[key]}]"
            )
        seen[key] = user["id"]


def _check_memberships(data: Data) -> None:
    """Check that a user has at most one membership per group."""
    seen: dict[tuple[str, str], str] = {}
    for membership in data["memberships"]:
        key = (membership["user_id"], membership["group_id"])
        if key in seen:
            raise StorageError(
                f"memberships[{membership['id']}] repeats user {key[0]} in group {key[1]}, "
                f"already in memberships[{seen[key]}]"
            )
        seen[key] = membership["id"]


def _check_event_type(event: Data) -> None:
    """Check the line, the result number and the event date against the event type."""
    label = f"events[{event['id']}]"
    over_under = event["type"] == "over_under"
    if over_under and event["line"] is None:
        raise StorageError(f"{label}.line must be set for an over_under event, got None")
    if not over_under and event["line"] is not None:
        raise StorageError(
            f"{label}.line must be null for a {event['type']} event, got {event['line']!r}"
        )
    if not over_under and event["result_value"] is not None:
        raise StorageError(
            f"{label}.result_value must be null for a {event['type']} event, "
            f"got {event['result_value']!r}"
        )
    if datetime.fromisoformat(event["close_time"]) > datetime.fromisoformat(event["event_time"]):
        raise StorageError(
            f"{label}.close_time must not be after event_time ({event['event_time']}), "
            f"got {event['close_time']}"
        )


def _check_outcome(event: Data) -> None:
    """Check that the outcome is set exactly when the event is resolved."""
    label = f"events[{event['id']}]"
    if event["status"] == "resolved" and event["outcome"] is None:
        raise StorageError(f"{label}.outcome must be yes or no when resolved, got None")
    if event["status"] != "resolved" and event["outcome"] is not None:
        raise StorageError(
            f"{label}.outcome must be null unless resolved, got {event['outcome']!r}"
        )


def _check_payouts(event: Data, bets: list[Data]) -> None:
    """Check that a resolved event pays out exactly its pool (sum of all stakes)."""
    event_bets = [bet for bet in bets if bet["event_id"] == event["id"]]
    for bet in event_bets:
        if bet["payout"] is None:
            raise StorageError(
                f"bets[{bet['id']}].payout must be set because events[{event['id']}] "
                "is resolved, got None"
            )
    pool = sum(bet["stake"] for bet in event_bets)
    paid = sum(bet["payout"] for bet in event_bets)
    if paid != pool:
        raise StorageError(
            f"events[{event['id']}] payouts must add up to the pool {pool}, got {paid}"
        )


def _check_refunds(event: Data, bets: list[Data]) -> None:
    """Check that every bet on a deleted event was refunded: payout equals stake."""
    for bet in bets:
        if bet["event_id"] == event["id"] and bet["payout"] != bet["stake"]:
            raise StorageError(
                f"bets[{bet['id']}].payout must equal the stake {bet['stake']} because "
                f"events[{event['id']}] is deleted, got {bet['payout']!r}"
            )


def _is_names_event(event: Data) -> bool:
    """Return True for a stored names event."""
    return event["type"] == TYPE_NAMES


def _check_names_fields(event: Data) -> None:
    """Check the names fields: all present on a names event, none on a yes/no one.

    On a names event the outcome stays null; the winners are option ids of
    the event, set exactly when it is resolved, and only one unless several
    can win.
    """
    label = f"events[{event['id']}]"
    if not _is_names_event(event):
        for field in NAMES_FIELDS:
            if field in event:
                raise StorageError(f"{label}.{field} is only allowed on a names event")
        return
    for field in NAMES_FIELDS:
        if field not in event:
            raise StorageError(f"{label}.{field} is missing")
    if event["outcome"] is not None:
        raise StorageError(f"{label}.outcome must be null on a names event, got {event['outcome']!r}")
    winners = event["winners"]
    if not set(winners) <= {option["id"] for option in event["options"]}:
        raise StorageError(f"{label}.winners must be ids of this event's options, got {winners!r}")
    if event["status"] != "resolved" and winners:
        raise StorageError(f"{label}.winners must be empty unless resolved, got {winners!r}")
    if event["status"] == "resolved" and not winners:
        raise StorageError(f"{label}.winners must name at least one option when resolved")
    if len(winners) > 1 and not event["multi_winner"]:
        raise StorageError(f"{label}.winners must name exactly one option on a one-winner event")


def _check_bet_sides(data: Data) -> None:
    """Check that every bet's side fits its event: yes or no, or one of its option ids."""
    options = {event["id"]: {option["id"] for option in event["options"]}
               for event in data["events"] if _is_names_event(event)}
    for bet in data["bets"]:
        if bet["event_id"] in options:
            if bet["side"] not in options[bet["event_id"]]:
                raise StorageError(
                    f"bets[{bet['id']}].side must be an option id of events[{bet['event_id']}], "
                    f"got {bet['side']!r}"
                )
        elif bet["side"] not in ("yes", "no"):
            raise StorageError(f"bets[{bet['id']}].side must be yes or no, got {bet['side']!r}")


def _check_events(data: Data) -> None:
    """Run the names, side, outcome, payout and refund checks for every event."""
    for event in data["events"]:
        _check_event_type(event)
        _check_names_fields(event)
    _check_bet_sides(data)
    for event in data["events"]:
        if not _is_names_event(event):
            _check_outcome(event)
        if event["status"] == "resolved":
            _check_payouts(event, data["bets"])
        elif event["status"] == "deleted":
            _check_refunds(event, data["bets"])


def validate(data: Any) -> None:
    """Check the whole data structure; raise StorageError naming section, id and field."""
    _check_top_level(data)
    ids = _collect_ids(data)
    for section in SECTIONS:
        for index, record in enumerate(data[section]):
            _check_record(section, record, index, ids)
    _check_usernames(data)
    _check_memberships(data)
    _check_events(data)


# ---------------------------------------------------------------------------
# Paths and file helpers
# ---------------------------------------------------------------------------

def _resolve_path(path: PathArg) -> Path:
    """Return the data file: the argument, else $DATA_FILE, else repo/data/data.json."""
    if path is not None:
        return Path(path)
    override = os.environ.get("DATA_FILE")
    if override:
        return Path(override)
    return DEFAULT_DATA_FILE


def _backup_dir(path: Path) -> Path:
    """Return the folder that holds the backups of ``path``."""
    return path.parent / "backups"


def _unique_path(directory: Path, prefix: str) -> Path:
    """Return a new '<prefix><UTC timestamp>.json' path that does not exist yet.

    The timestamp has microseconds and a fixed width, so names sort chronologically.
    If the name is taken, the timestamp moves forward one microsecond.
    """
    moment = datetime.now(timezone.utc)
    while True:
        candidate = directory / f"{prefix}{moment.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
        if not candidate.exists():
            return candidate
        moment += timedelta(microseconds=1)


def _replace(source: str | Path, target: Path) -> None:
    """Call os.replace; on Windows retry for up to ~1 second while the target is busy."""
    attempts = REPLACE_ATTEMPTS if RETRY_REPLACE else 1
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(REPLACE_DELAY_SECONDS)


def _write_atomic(path: Path, payload: bytes) -> None:
    """Write bytes to a temp file next to ``path``, fsync, then os.replace onto ``path``.

    The temp file is removed on any error, which is then re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(temp_name)
        raise


def _copy_to_backup(path: Path) -> None:
    """Copy the current main file into the backup folder; never overwrite a backup."""
    directory = _backup_dir(path)
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        target = _unique_path(directory, "data-")
        try:
            with open(path, "rb") as source, open(target, "xb") as destination:
                shutil.copyfileobj(source, destination)
            return
        except FileExistsError:
            continue


def _prune_backups(path: Path) -> None:
    """Delete backups beyond the newest MAX_BACKUPS (a failed delete is ignored)."""
    for old in list_backups(path)[MAX_BACKUPS:]:
        with contextlib.suppress(OSError):
            old.unlink()


# ---------------------------------------------------------------------------
# Database (PostgreSQL / Supabase): one row holds the whole data dict as JSON
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS app_data (
    id integer PRIMARY KEY CHECK (id = 1),
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_data_backups (
    id bigserial PRIMARY KEY,
    saved_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('backup', 'corrupt')),
    data jsonb NOT NULL
);
ALTER TABLE app_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data_backups ENABLE ROW LEVEL SECURITY;
"""
# Row level security with no policies blocks Supabase's public web API from these
# tables (they hold password hashes). Our own connection is the table owner, so it
# is not affected.


class _SecretUrl(str):
    """A database URL that never shows itself in tracebacks or logs.

    It works like a normal string, but repr() hides it, so the password inside the
    URL is not printed when an error report lists function arguments (pytest does).
    """

    def __repr__(self) -> str:
        """Return a placeholder instead of the URL."""
        return "'<database url hidden>'"


def _database_url(path: PathArg) -> str | None:
    """Return the database URL to use, or None to use the file.

    A path argument always means the file, and so does $DATA_FILE: whoever sets it
    chose a local file on purpose (Streamlit copies secrets such as DATABASE_URL
    into the environment by itself, so this is the way to stay off the database).
    Otherwise $DATABASE_URL, if set and not empty, selects the database. The URL
    is wrapped so it is never printed.
    """
    if path is not None or os.environ.get("DATA_FILE"):
        return None
    value = os.environ.get(DATABASE_URL_ENV)
    return _SecretUrl(value) if value else None


def _psycopg() -> Any:
    """Import and return psycopg, or raise StorageError if it is not installed."""
    try:
        import psycopg
    except ImportError as error:
        raise StorageError(
            f"{DATABASE_URL_ENV} is set but psycopg is not installed"
        ) from error
    return psycopg


def _connect(psycopg: Any, url: str) -> Any:
    """Open a connection; prepared statements are off so Supabase poolers work.

    Autocommit is on: each use wraps its work in conn.transaction(), so an idle
    connection never sits inside an open transaction.
    """
    return psycopg.connect(
        url, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS, prepare_threshold=None,
        autocommit=True,
    )


# Opening a database connection costs several network round trips (often more
# than the query itself), so finished connections are kept and reused. Each
# connection is used by one caller at a time.
MAX_IDLE_CONNECTIONS = 4
IDLE_CHECK_SECONDS = 60

_POOL_LOCK = threading.Lock()
_IDLE: dict[str, list[tuple[Any, float]]] = {}


def _is_reusable(conn: Any) -> bool:
    """Return True if the connection is open and not inside a transaction."""
    from psycopg.pq import TransactionStatus

    return not conn.closed and conn.info.transaction_status == TransactionStatus.IDLE


def _still_alive(conn: Any) -> bool:
    """Return True if a quick query succeeds (the server may drop idle connections)."""
    try:
        conn.execute("SELECT 1")
    except Exception:  # any failure means: do not use this connection
        return False
    return True


def _close_quietly(conn: Any) -> None:
    """Close a connection, ignoring errors (it may already be broken)."""
    with contextlib.suppress(Exception):
        conn.close()


def _take_connection(psycopg: Any, url: str) -> Any:
    """Return a reusable idle connection, or open a new one.

    A connection idle for longer than IDLE_CHECK_SECONDS is tested first, because
    the server may have closed it in the meantime.
    """
    now = time.monotonic()
    while True:
        with _POOL_LOCK:
            idle = _IDLE.get(url)
            if not idle:
                break
            conn, since = idle.pop()
        if not _is_reusable(conn):
            _close_quietly(conn)
        elif now - since > IDLE_CHECK_SECONDS and not _still_alive(conn):
            _close_quietly(conn)
        else:
            return conn
    return _connect(psycopg, url)


def _give_back_connection(url: str, conn: Any) -> None:
    """Keep a healthy connection for the next caller; close it if broken or surplus."""
    if not _is_reusable(conn):
        _close_quietly(conn)
        return
    with _POOL_LOCK:
        idle = _IDLE.setdefault(url, [])
        if len(idle) < MAX_IDLE_CONNECTIONS:
            idle.append((conn, time.monotonic()))
            return
    _close_quietly(conn)


def close_connections() -> None:
    """Close all kept database connections (for tests and clean shutdown)."""
    with _POOL_LOCK:
        kept = [conn for idle in _IDLE.values() for conn, _ in idle]
        _IDLE.clear()
    for conn in kept:
        _close_quietly(conn)


def _database_error(error: Exception) -> StorageError:
    """Wrap a psycopg error in a StorageError (psycopg never shows the password)."""
    return StorageError(f"database error ({type(error).__name__}): {error}".strip())


def _ensure_tables(psycopg: Any, url: str) -> None:
    """Create the tables once per process, in their own committed transaction."""
    if url in _READY_DATABASES:
        return
    with _connect(psycopg, url) as conn:
        conn.execute(_CREATE_TABLES)
    _READY_DATABASES.add(url)


@contextlib.contextmanager
def _db_cursor(url: str, in_transaction: bool = True) -> Iterator[Any]:
    """Yield a cursor inside one database transaction.

    The transaction commits when the block ends and rolls back if it raises.
    With in_transaction=False there is no BEGIN/COMMIT (two fewer network round
    trips); use that only for a single read, which is consistent on its own.
    The connection is reused afterwards (see _take_connection). Database errors
    become StorageError; other errors pass through unchanged.
    """
    psycopg = _psycopg()
    try:
        _ensure_tables(psycopg, url)
        conn = _take_connection(psycopg, url)
        try:
            if in_transaction:
                with conn.transaction(), conn.cursor() as cur:
                    yield cur
            else:
                with conn.cursor() as cur:
                    yield cur
        finally:
            _give_back_connection(url, conn)
    except psycopg.Error as error:
        raise _database_error(error) from error


def _db_read(cur: Any, lock: bool = False) -> Any:
    """Return the stored data (not yet validated), or None if nothing is saved yet."""
    suffix = " FOR UPDATE" if lock else ""
    cur.execute("SELECT data FROM app_data WHERE id = 1" + suffix)
    row = cur.fetchone()
    return None if row is None else row[0]


def _db_validated(data: Any) -> Data:
    """Validate data read from the database; None (nothing saved) gives empty_data()."""
    if data is None:
        return empty_data()
    data = upgrade(data)
    try:
        validate(data)
    except StorageError as error:
        raise StorageError(f"the stored data is invalid: {error}") from error
    return data


def _db_add_backup(cur: Any, data: Any, kind: str) -> int:
    """Store a copy of data as a backup row; return the new row id."""
    from psycopg.types.json import Jsonb

    cur.execute(
        "INSERT INTO app_data_backups (kind, data) VALUES (%s, %s) RETURNING id",
        (kind, Jsonb(data)),
    )
    return cur.fetchone()[0]


def _db_write(cur: Any, data: Data) -> None:
    """Insert or replace the data row."""
    from psycopg.types.json import Jsonb

    cur.execute(
        "INSERT INTO app_data (id, data, updated_at) VALUES (1, %s, now()) "
        "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
        (Jsonb(data),),
    )


def _db_prune(cur: Any) -> None:
    """Keep only the newest MAX_BACKUPS rows of each backup kind (one statement)."""
    cur.execute(
        "DELETE FROM app_data_backups WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER "
        "(PARTITION BY kind ORDER BY id DESC) AS position FROM app_data_backups) AS ranked "
        "WHERE position > %s)",
        (MAX_BACKUPS,),
    )


def _db_save(cur: Any, data: Data, previous: Any) -> None:
    """Back up the previous data (if any), write the new data, prune old backups.

    The row must already be locked (previous was read FOR UPDATE in this
    transaction). The backup is copied inside the database instead of sending
    the old data back over the network, and the three statements go out together
    in one pipeline, so a save costs one round trip instead of four.
    """
    with cur.connection.pipeline():
        if previous is not None:
            cur.execute(
                "INSERT INTO app_data_backups (kind, data) "
                "SELECT 'backup', data FROM app_data WHERE id = 1"
            )
        _db_write(cur, data)
        _db_prune(cur)


def _backup_name(row_id: int, saved_at: datetime) -> str:
    """Return a readable backup name like 'backup-42-2026-11-16T13:22:55Z'."""
    moment = saved_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"backup-{row_id}-{moment}"


def _db_list_backups(url: str) -> list[str]:
    """Return the names of the database backups, newest first."""
    with _db_cursor(url) as cur:
        cur.execute(
            "SELECT id, saved_at FROM app_data_backups WHERE kind = 'backup' "
            "ORDER BY id DESC"
        )
        return [_backup_name(row_id, saved_at) for row_id, saved_at in cur.fetchall()]


def _is_valid_stored(data: Any) -> bool:
    """Return True if stored data is missing (nothing saved) or validates."""
    try:
        _db_validated(data)
    except StorageError:
        return False
    return True


def _db_restore(url: str, only_if_broken: bool) -> tuple[str, str | None] | None:
    """Restore the newest valid database backup (lock must be held).

    The broken data is kept as a 'corrupt' row. With only_if_broken, nothing is
    restored if the data loads now. Returns (backup name, corrupt name) or None.
    """
    with _db_cursor(url) as cur:
        current = _db_read(cur, lock=True)
        if only_if_broken and _is_valid_stored(current):
            return None
        cur.execute(
            "SELECT id, saved_at, data FROM app_data_backups WHERE kind = 'backup' "
            "ORDER BY id DESC"
        )
        problems: list[str] = []
        for row_id, saved_at, data in cur.fetchall():
            name = _backup_name(row_id, saved_at)
            data = upgrade(data)
            try:
                validate(data)
            except StorageError as error:
                problems.append(f"{name}: {error}")
                continue
            corrupt = None
            if current is not None:
                corrupt = f"corrupt row {_db_add_backup(cur, current, 'corrupt')}"
            _db_write(cur, data)
            return name, corrupt
    detail = "; ".join(problems) if problems else "no backups found"
    raise StorageError(f"no valid backup to restore ({detail})")


@contextlib.contextmanager
def _db_transaction(url: str) -> Iterator[Data]:
    """Database version of transaction(): lock the row, yield the data, save it.

    SELECT ... FOR UPDATE makes other app processes wait, so two users saving at the
    same time cannot overwrite each other. If the block raises, everything rolls back.
    """
    with _db_cursor(url) as cur:
        previous = _db_read(cur, lock=True)
        data = _db_validated(previous)
        yield data
        validate(data)
        _db_save(cur, data, previous)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_data(path: PathArg = None) -> Data:
    """Load and validate the data file (or the database row, see the module docstring).

    A missing file or empty database returns empty_data() and creates nothing.
    Unreadable files, invalid JSON, a wrong schema_version, failed validation or a
    database error raise StorageError.
    """
    url = _database_url(path)
    if url is not None:
        with _db_cursor(url, in_transaction=False) as cur:
            return _db_validated(_db_read(cur))
    file = _resolve_path(path)
    if not file.exists():
        return empty_data()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise StorageError(f"cannot read {file.name}: {error}") from error
    except json.JSONDecodeError as error:
        raise StorageError(f"{file.name} is not valid JSON: {error}") from error
    data = upgrade(data)
    try:
        validate(data)
    except StorageError as error:
        raise StorageError(f"{file.name} is invalid: {error}") from error
    return data


def save_data(data: Data, path: PathArg = None) -> None:
    """Validate and save the data atomically, keeping a backup of the previous file.

    Invalid data raises before anything on disk is touched. Otherwise: back up the
    existing file, write a temp file in the same folder, fsync, os.replace it onto the
    main file, then prune old backups. The temp file is removed on any error.
    """
    validate(data)
    url = _database_url(path)
    if url is not None:
        with _db_cursor(url) as cur:
            _db_save(cur, data, _db_read(cur, lock=True))
        return
    file = _resolve_path(path)
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    if file.exists():
        _copy_to_backup(file)
    _write_atomic(file, payload)
    _prune_backups(file)


@contextlib.contextmanager
def transaction(path: PathArg = None) -> Iterator[Data]:
    """Load the data under a lock, yield it for changes, then save it.

    All code that changes data must use this, including closing events and ending
    polls when a page opens. Read-only pages may call load_data directly: the atomic
    replace means they always read a complete file. If the block raises, nothing is
    saved and the error is re-raised. A nested transaction in the same thread raises
    StorageError instead of deadlocking. For the file, the lock covers threads of one
    process only; in the database, a row lock also makes other processes wait.
    """
    if getattr(_STATE, "active", False):
        raise StorageError("nested transaction in the same thread is not allowed")
    url = _database_url(path)
    with _LOCK:
        _STATE.active = True
        try:
            if url is not None:
                with _db_transaction(url) as data:
                    yield data
            else:
                data = load_data(path)
                yield data
                save_data(data, path)
        finally:
            _STATE.active = False


def list_backups(path: PathArg = None) -> list[Path] | list[str]:
    """Return the backups, newest first: file paths, or backup names in the database."""
    url = _database_url(path)
    if url is not None:
        return _db_list_backups(url)
    directory = _backup_dir(_resolve_path(path))
    if not directory.is_dir():
        return []
    return sorted(directory.glob("data-*.json"), key=lambda item: item.name, reverse=True)


def _restore_move_main(file: Path) -> Path | None:
    """Move an existing main file to data.corrupt-<timestamp>.json; return the new path."""
    if not file.exists():
        return None
    corrupt = _unique_path(file.parent, "data.corrupt-")
    _replace(file, corrupt)
    return corrupt


def _restore_locked(file: Path) -> tuple[Path, Path | None]:
    """Restore the newest valid backup (lock must be held); return (backup, corrupt file)."""
    problems: list[str] = []
    for backup in list_backups(file):
        try:
            load_data(backup)
            payload = backup.read_bytes()
        except (StorageError, OSError) as error:
            problems.append(f"{backup.name}: {error}")
            continue
        corrupt = _restore_move_main(file)
        try:
            _write_atomic(file, payload)
        except OSError as error:
            if corrupt is not None:
                with contextlib.suppress(OSError):
                    _replace(corrupt, file)
            raise StorageError(f"cannot restore {backup.name}: {error}") from error
        return backup, corrupt
    detail = "; ".join(problems) if problems else "no backups found"
    raise StorageError(f"no valid backup to restore ({detail})")


def _is_loadable(file: Path) -> bool:
    """Return True if the file loads and validates (a missing file counts as loadable)."""
    try:
        load_data(file)
    except StorageError:
        return False
    return True


def _restore(
    path: PathArg, only_if_broken: bool = False
) -> tuple[Path | str, Path | str | None] | None:
    """Take the lock and restore the newest valid backup; refuse inside a transaction.

    With only_if_broken, the main file is checked again once the lock is held. If it
    loads now (another session restored or saved it meanwhile), nothing is restored and
    None is returned, so a newer good file is never replaced by an older backup.
    In the database, backups and the broken copy are named by their row instead.
    """
    if getattr(_STATE, "active", False):
        raise StorageError("cannot restore a backup inside a transaction")
    url = _database_url(path)
    if url is not None:
        with _LOCK:
            return _db_restore(url, only_if_broken)
    file = _resolve_path(path)
    with _LOCK:
        if only_if_broken and _is_loadable(file):
            return None
        return _restore_locked(file)


def restore_latest_backup(path: PathArg = None) -> Path | str:
    """Restore the newest backup that loads and validates; return that backup's path.

    In the database, the backup's name is returned instead of a path.

    Runs under the lock, so it must not be called inside a transaction. The broken main
    file (if any) is first moved to data.corrupt-<timestamp>.json next to it. With no
    valid backup, StorageError is raised and the main file stays where it is.
    """
    restored = _restore(path)
    if restored is None:  # cannot happen: None only comes with only_if_broken
        raise StorageError("restore returned no backup")
    return restored[0]


def load_with_recovery(path: PathArg = None) -> tuple[Data, str | None]:
    """Load the data for app start-up, restoring a backup if the file is broken.

    Returns (data, None) when nothing is wrong, or (data, warning) after a restore; the
    warning names the backup used and where the broken file was moved. If another
    session fixed the file in the meantime, nothing is restored and no warning is
    returned. If the restore fails too, StorageError carries both reasons.
    """
    try:
        return load_data(path), None
    except StorageError as load_error:
        reason = str(load_error)
        try:
            restored = _restore(path, only_if_broken=True)
        except StorageError as restore_error:
            raise StorageError(f"{reason}; recovery failed: {restore_error}") from restore_error
    if restored is None:
        return load_data(path), None
    backup, corrupt = restored
    if isinstance(backup, str):
        warning = f"The stored data was damaged ({reason}). Restored from {backup}."
        if corrupt is not None:
            warning += f" The broken data was kept as {corrupt}."
        return load_data(path), warning
    warning = f"The data file was damaged ({reason}). Restored from backup {backup.name}."
    if corrupt is not None:
        warning += f" The broken file was moved to {corrupt}."
    return load_data(path), warning

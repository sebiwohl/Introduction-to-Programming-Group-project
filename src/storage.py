"""Storage module: the only place that reads or writes the data files.

Data lives in one JSON file (``data/data.json`` by default). Every save is
validated first, written to a temporary file and moved into place atomically.
The previous version is kept as a backup (the newest ``MAX_BACKUPS`` are
kept), so a damaged file can be restored at start-up.

Standard library only, no Streamlit.
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
from typing import Any

SCHEMA_VERSION = 1
MAX_BACKUPS = 20
DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "data.json"
SECTIONS = ("users", "groups", "events", "bets", "polls")

# On Windows, os.replace fails with PermissionError while another thread or process
# (or a virus scanner) has the target open, so the replace is retried briefly there.
RETRY_REPLACE = os.name == "nt"
REPLACE_ATTEMPTS = 20
REPLACE_DELAY_SECONDS = 0.05

Data = dict[str, Any]
PathArg = str | os.PathLike[str] | None
Predicate = Callable[[Any, dict[str, set[str]]], bool]

_LOCK = threading.Lock()
_STATE = threading.local()


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


def _nullable(predicate: Predicate) -> Predicate:
    """Wrap a predicate so that None is accepted as well."""
    return partial(_or_null, predicate)


Rule = tuple[Predicate, str]

RULES: dict[str, dict[str, Rule]] = {
    "users": {
        "id": (_is_text, "a non-empty string"),
        "username": (_is_text, "a non-empty string"),
        "password_hash": (_is_text, "a non-empty string"),
        "group_id": (_nullable(partial(_is_ref, "groups")), "an existing group id or null"),
        "role": (partial(_is_choice, ("member", "admin")), "member or admin"),
        "balance": (partial(_is_int_min, 0), "a whole number >= 0"),
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
    },
    "bets": {
        "id": (_is_text, "a non-empty string"),
        "event_id": (partial(_is_ref, "events"), "an existing event id"),
        "user_id": (partial(_is_ref, "users"), "an existing user id"),
        "side": (partial(_is_choice, ("yes", "no")), "yes or no"),
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
}


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
    """Check that a record has exactly the allowed fields and that each value is valid."""
    label = _label(section, record, index)
    rules = RULES[section]
    for field in sorted(rules.keys() - record.keys()):
        raise StorageError(f"{label}.{field} is missing")
    for field in sorted(map(str, record.keys() - rules.keys())):
        raise StorageError(f"{label}.{field} is not an allowed field")
    for field, (predicate, description) in rules.items():
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


def _check_events(data: Data) -> None:
    """Run the outcome, payout and refund checks for every event."""
    for event in data["events"]:
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
# Public API
# ---------------------------------------------------------------------------

def load_data(path: PathArg = None) -> Data:
    """Load and validate the data file.

    A missing file returns empty_data() and creates nothing. Unreadable files, invalid
    JSON, a wrong schema_version or failed validation raise StorageError.
    """
    file = _resolve_path(path)
    if not file.exists():
        return empty_data()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise StorageError(f"cannot read {file.name}: {error}") from error
    except json.JSONDecodeError as error:
        raise StorageError(f"{file.name} is not valid JSON: {error}") from error
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
    StorageError instead of deadlocking. The lock covers threads of one process only.
    """
    if getattr(_STATE, "active", False):
        raise StorageError("nested transaction in the same thread is not allowed")
    with _LOCK:
        _STATE.active = True
        try:
            data = load_data(path)
            yield data
            save_data(data, path)
        finally:
            _STATE.active = False


def list_backups(path: PathArg = None) -> list[Path]:
    """Return the backup files of the data file, newest first."""
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


def _restore(path: PathArg, only_if_broken: bool = False) -> tuple[Path, Path | None] | None:
    """Take the lock and restore the newest valid backup; refuse inside a transaction.

    With only_if_broken, the main file is checked again once the lock is held. If it
    loads now (another session restored or saved it meanwhile), nothing is restored and
    None is returned, so a newer good file is never replaced by an older backup.
    """
    if getattr(_STATE, "active", False):
        raise StorageError("cannot restore a backup inside a transaction")
    file = _resolve_path(path)
    with _LOCK:
        if only_if_broken and _is_loadable(file):
            return None
        return _restore_locked(file)


def restore_latest_backup(path: PathArg = None) -> Path:
    """Restore the newest backup that loads and validates; return that backup's path.

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
    warning = f"The data file was damaged ({reason}). Restored from backup {backup.name}."
    if corrupt is not None:
        warning += f" The broken file was moved to {corrupt}."
    return load_data(path), warning

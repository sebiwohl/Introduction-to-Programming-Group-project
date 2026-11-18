"""Tests for src/storage.py. Every test uses a temporary path, never the repo's data/."""

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from src import storage
from src.storage import StorageError


def yes_no(close_time: str) -> dict[str, Any]:
    """Return the version 2 event fields of a yes/no event that happens when betting closes."""
    return {"type": "yes_no", "line": None, "event_time": close_time, "result_value": None}


def build_valid_data() -> dict[str, Any]:
    """Build a valid data set: one group, open event with bets, resolved event, poll."""
    data = storage.empty_data()
    data["users"] = [
        {"id": "u1", "username": "anna", "password_hash": "h1"},
        {"id": "u2", "username": "ben", "password_hash": "h2"},
        {"id": "u3", "username": "carl", "password_hash": "h3"},
    ]
    data["memberships"] = [
        {"id": "m1", "user_id": "u1", "group_id": "g1", "role": "admin", "balance": 1000,
         "restricted": False},
        {"id": "m2", "user_id": "u2", "group_id": "g1", "role": "member", "balance": 990,
         "restricted": False},
    ]
    data["groups"] = [
        {"id": "g1", "name": "Friends", "admin_id": "u1", "invite_code": "ABC123",
         "start_balance": 1000},
    ]
    data["events"] = [
        {"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "Anna is late",
         "description": "", "close_time": "2026-11-20T19:00:00+00:00", "status": "open",
         "outcome": None, "flags": [], **yes_no("2026-11-20T19:00:00+00:00")},
        {"id": "e2", "group_id": "g1", "creator_id": "u2", "title": "It rains",
         "description": "On Saturday", "close_time": "2026-11-10T12:00:00+00:00",
         "status": "resolved", "outcome": "yes", "flags": ["u3"],
         **yes_no("2026-11-10T12:00:00+00:00")},
        {"id": "e3", "group_id": "g1", "creator_id": "u2", "title": "Fishy event",
         "description": "", "close_time": "2026-11-25T12:00:00+00:00", "status": "paused",
         "outcome": None, "flags": ["u1", "u2"], **yes_no("2026-11-25T12:00:00+00:00")},
        {"id": "e4", "group_id": "g1", "creator_id": "u1", "title": "Goals on Sunday",
         "description": "", "close_time": "2026-11-29T15:00:00+00:00", "status": "open",
         "outcome": None, "flags": [], "type": "over_under", "line": "2.5",
         "event_time": "2026-11-29T16:00:00+00:00", "result_value": None},
    ]
    data["topups"] = [
        {"id": "t1", "group_id": "g1", "amount": 1000, "created_by": "u1",
         "created_at": "2026-11-01T10:00:00+00:00"},
    ]
    data["repayments"] = [
        {"id": "r1", "group_id": "g1", "created_by": "u1",
         "created_at": "2026-11-01T09:00:00+00:00"},
    ]
    data["notifications"] = [
        {"id": "n1", "user_id": "u1", "group_id": "g1", "text": "Ben is running low.",
         "created_at": "2026-11-12T08:00:00+00:00", "read": False},
    ]
    data["bets"] = [
        {"id": "b1", "event_id": "e1", "user_id": "u1", "side": "yes", "stake": 10,
         "payout": None},
        {"id": "b2", "event_id": "e1", "user_id": "u2", "side": "no", "stake": 10,
         "payout": None},
        {"id": "b3", "event_id": "e2", "user_id": "u1", "side": "yes", "stake": 10,
         "payout": 15},
        {"id": "b4", "event_id": "e2", "user_id": "u2", "side": "yes", "stake": 10,
         "payout": 15},
        {"id": "b5", "event_id": "e2", "user_id": "u3", "side": "no", "stake": 10,
         "payout": 0},
    ]
    data["polls"] = [
        {"id": "p1", "event_id": "e3", "opened_by": "auto",
         "closes_at": "2026-11-16T12:00:00+00:00", "votes": {"u1": "delete", "u2": "keep"},
         "result": None},
    ]
    return data


def find(data: dict[str, Any], section: str, record_id: str) -> dict[str, Any]:
    """Return the record with the given id from a section."""
    return next(record for record in data[section] if record["id"] == record_id)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    """Return a data file path inside a temp folder that does not exist yet."""
    return tmp_path / "data" / "data.json"


@pytest.fixture
def valid() -> dict[str, Any]:
    """Return a fresh valid data set."""
    return build_valid_data()


def save_balances(path: Path, count: int) -> None:
    """Save the valid data `count` times, with u1's balance set to 1, 2, ... count."""
    data = build_valid_data()
    for number in range(1, count + 1):
        data["memberships"][0]["balance"] = number
        storage.save_data(data, path)


def balance_of(path: Path) -> int:
    """Return u1's balance as stored in the given file."""
    return find(storage.load_data(path), "memberships", "m1")["balance"]


# 1 -------------------------------------------------------------------------

def test_missing_file_returns_empty_data_and_creates_nothing(path: Path) -> None:
    """A missing file gives empty data and no file or folder is created."""
    assert storage.load_data(path) == storage.empty_data()
    assert not path.exists()
    assert not path.parent.exists()


def test_empty_data_returns_new_lists_each_time() -> None:
    """Changing one empty_data() result does not affect the next one."""
    first = storage.empty_data()
    first["users"].append({"id": "x"})
    assert storage.empty_data()["users"] == []


# 2 -------------------------------------------------------------------------

def test_save_then_load_round_trips(path: Path, valid: dict[str, Any]) -> None:
    """Data read back after saving is identical to what was saved."""
    storage.save_data(valid, path)
    assert storage.load_data(path) == valid


# 3 -------------------------------------------------------------------------

def test_broken_json_raises(path: Path) -> None:
    """A file that is not JSON raises StorageError."""
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(StorageError):
        storage.load_data(path)


def test_missing_section_raises(path: Path, valid: dict[str, Any]) -> None:
    """A file without one of the sections raises StorageError."""
    del valid["polls"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(StorageError, match="polls"):
        storage.load_data(path)


def test_wrong_schema_version_raises(path: Path, valid: dict[str, Any]) -> None:
    """A different schema_version raises StorageError."""
    valid["schema_version"] = 3
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(StorageError, match="schema_version"):
        storage.load_data(path)


def test_invalid_utf8_raises(path: Path) -> None:
    """A file with undecodable bytes raises StorageError."""
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(StorageError):
        storage.load_data(path)


# 4 -------------------------------------------------------------------------

def test_valid_fixture_passes_validate(valid: dict[str, Any]) -> None:
    """The reference fixture is accepted."""
    storage.validate(valid)


def test_new_user_without_group_is_valid(valid: dict[str, Any]) -> None:
    """A user without a group (no membership) is accepted."""
    assert all(m["user_id"] != "u3" for m in valid["memberships"])
    storage.validate(valid)


def test_resolved_event_without_bets_is_valid(valid: dict[str, Any]) -> None:
    """A resolved event with no bets has pool 0 and zero payouts."""
    valid["bets"] = [bet for bet in valid["bets"] if bet["event_id"] != "e2"]
    storage.validate(valid)


def delete_event_with_bet(data: dict[str, Any], payout: Any) -> None:
    """Mark e3 deleted and give it one bet of stake 7 with the given payout."""
    find(data, "events", "e3")["status"] = "deleted"
    data["bets"].append({"id": "b9", "event_id": "e3", "user_id": "u1", "side": "yes",
                         "stake": 7, "payout": payout})


def test_deleted_event_with_full_refunds_is_saved(path: Path, valid: dict[str, Any]) -> None:
    """A deleted event whose bets are all paid back their stake is accepted."""
    delete_event_with_bet(valid, 7)
    storage.save_data(valid, path)
    assert storage.load_data(path) == valid


def test_deleted_event_without_bets_is_saved(path: Path, valid: dict[str, Any]) -> None:
    """A deleted event with no bets has nothing to refund."""
    find(valid, "events", "e3")["status"] = "deleted"
    storage.save_data(valid, path)
    assert storage.load_data(path) == valid


def test_username_is_stored_as_given(path: Path, valid: dict[str, Any]) -> None:
    """Spaces and case in a username are kept exactly when saved."""
    find(valid, "users", "u3")["username"] = "  Carl Big "
    storage.save_data(valid, path)
    assert find(storage.load_data(path), "users", "u3")["username"] == "  Carl Big "


def test_usernames_that_differ_after_trimming_are_saved(
    path: Path, valid: dict[str, Any]
) -> None:
    """'anna' and 'anna2' are different names and are both accepted."""
    find(valid, "users", "u2")["username"] = " Anna2"
    storage.save_data(valid, path)
    assert storage.load_data(path) == valid


@pytest.mark.parametrize("moment", [
    "2026-11-20T18:00:00+00:00",
    "2026-11-20T19:00:00+01:00",
    "2026-11-20T12:30:00-05:30",
    "2026-11-20T18:00:00.250000+00:00",
    "2026-11-20T18:00:00Z",
])
def test_times_with_offset_are_saved(path: Path, valid: dict[str, Any], moment: str) -> None:
    """close_time and closes_at with a timezone offset are accepted."""
    find(valid, "events", "e1")["close_time"] = moment
    find(valid, "polls", "p1")["closes_at"] = moment
    storage.save_data(valid, path)
    assert storage.load_data(path) == valid


def test_error_message_names_section_id_and_field(valid: dict[str, Any]) -> None:
    """The message points at the exact record and field."""
    find(valid, "bets", "b1")["stake"] = 12.5
    with pytest.raises(StorageError) as info:
        storage.validate(valid)
    assert str(info.value) == "bets[b1].stake must be a whole number > 0, got 12.5"


# 5 -------------------------------------------------------------------------

def set_field(section: str, record_id: str, field: str, value: Any) -> Callable[[dict], None]:
    """Return a function that sets one field of one record."""
    def mutate(data: dict[str, Any]) -> None:
        """Set the field on the record."""
        find(data, section, record_id)[field] = value
    return mutate


def duplicate_id(data: dict[str, Any]) -> None:
    """Give the second user the id of the first."""
    data["users"][1]["id"] = "u1"


def add_extra_field(data: dict[str, Any]) -> None:
    """Add a field that is not in the format."""
    find(data, "events", "e1")["colour"] = "red"


def remove_field(data: dict[str, Any]) -> None:
    """Remove a required field."""
    del find(data, "events", "e1")["title"]


def break_payouts(data: dict[str, Any]) -> None:
    """Make the resolved event pay out one coin too many."""
    find(data, "bets", "b3")["payout"] += 1


def missing_payout(data: dict[str, Any]) -> None:
    """Remove a payout from a resolved event."""
    find(data, "bets", "b5")["payout"] = None


def refund_missing(data: dict[str, Any]) -> None:
    """Delete an event but leave a bet on it without a payout."""
    delete_event_with_bet(data, None)


def refund_too_small(data: dict[str, Any]) -> None:
    """Delete an event but refund less than the stake."""
    delete_event_with_bet(data, 6)


def refund_too_large(data: dict[str, Any]) -> None:
    """Delete an event but refund more than the stake."""
    delete_event_with_bet(data, 8)


def duplicate_flag(data: dict[str, Any]) -> None:
    """Let the same user flag an event twice."""
    find(data, "events", "e3")["flags"] = ["u1", "u1"]


INVALID_CHANGES = {
    "float stake": set_field("bets", "b1", "stake", 12.5),
    "boolean stake": set_field("bets", "b1", "stake", True),
    "zero stake": set_field("bets", "b1", "stake", 0),
    "negative balance": set_field("memberships", "m2", "balance", -1),
    "float balance": set_field("memberships", "m2", "balance", 10.0),
    "boolean balance": set_field("memberships", "m2", "balance", False),
    "zero start balance": set_field("groups", "g1", "start_balance", 0),
    "float payout": set_field("bets", "b3", "payout", 15.0),
    "unknown status": set_field("events", "e1", "status", "archived"),
    "bad side": set_field("bets", "b1", "side", "maybe"),
    "bad role": set_field("memberships", "m1", "role", "owner"),
    "bet on missing event": set_field("bets", "b1", "event_id", "nope"),
    "bet by missing user": set_field("bets", "b1", "user_id", "nope"),
    "user in missing group": set_field("memberships", "m1", "group_id", "nope"),
    "flag by unknown user": set_field("events", "e1", "flags", ["ghost"]),
    "duplicate flag": duplicate_flag,
    "duplicate id": duplicate_id,
    "duplicate username": set_field("users", "u2", "username", "anna"),
    "username clash by case": set_field("users", "u2", "username", "ANNA"),
    "username clash by spaces": set_field("users", "u2", "username", " anna "),
    "username clash by case and spaces": set_field("users", "u2", "username", " Anna"),
    "deleted event bet without refund": refund_missing,
    "deleted event refund below stake": refund_too_small,
    "deleted event refund above stake": refund_too_large,
    "close_time without offset": set_field("events", "e1", "close_time", "2026-11-20T19:00"),
    "close_time date only": set_field("events", "e1", "close_time", "2026-11-20"),
    "closes_at without offset": set_field("polls", "p1", "closes_at", "2026-11-16T12:00:00"),
    "empty username": set_field("users", "u2", "username", "  "),
    "empty password hash": set_field("users", "u2", "password_hash", ""),
    "unknown extra field": add_extra_field,
    "missing field": remove_field,
    "unparsable close_time": set_field("events", "e1", "close_time", "next tuesday"),
    "resolved without outcome": set_field("events", "e2", "outcome", None),
    "open with outcome": set_field("events", "e1", "outcome", "yes"),
    "bad outcome": set_field("events", "e2", "outcome", "maybe"),
    "payouts do not add up": break_payouts,
    "resolved bet without payout": missing_payout,
    "poll vote maybe": set_field("polls", "p1", "votes", {"u1": "maybe"}),
    "poll vote by unknown user": set_field("polls", "p1", "votes", {"ghost": "keep"}),
    "poll opened by unknown user": set_field("polls", "p1", "opened_by", "ghost"),
    "bad poll result": set_field("polls", "p1", "result", "tie"),
    "unparsable closes_at": set_field("polls", "p1", "closes_at", "soon"),
    "membership by missing user": set_field("memberships", "m2", "user_id", "ghost"),
    "membership repeated": set_field("memberships", "m2", "user_id", "u1"),
    "restricted not a boolean": set_field("memberships", "m1", "restricted", 0),
    "unknown event type": set_field("events", "e1", "type", "multiple"),
    "over_under without line": set_field("events", "e4", "line", None),
    "line as a float": set_field("events", "e4", "line", 2.5),
    "line not a number": set_field("events", "e4", "line", "two"),
    "yes_no with a line": set_field("events", "e1", "line", "2.5"),
    "yes_no with a result value": set_field("events", "e1", "result_value", "3"),
    "betting closes after the event": set_field("events", "e4", "event_time",
                                                "2026-11-29T14:00:00+00:00"),
    "event_time without offset": set_field("events", "e4", "event_time", "2026-11-29T16:00"),
    "zero top-up": set_field("topups", "t1", "amount", 0),
    "top-up in missing group": set_field("topups", "t1", "group_id", "nope"),
    "repayment by unknown user": set_field("repayments", "r1", "created_by", "ghost"),
    "notification read not a boolean": set_field("notifications", "n1", "read", "no"),
    "empty notification": set_field("notifications", "n1", "text", " "),
}


@pytest.mark.parametrize("change", INVALID_CHANGES.values(), ids=INVALID_CHANGES.keys())
def test_save_refuses_invalid_data_and_keeps_file(
    path: Path, valid: dict[str, Any], change: Callable[[dict], None]
) -> None:
    """Invalid data raises StorageError; the file and the backups stay untouched."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    bad = copy.deepcopy(valid)
    change(bad)
    with pytest.raises(StorageError):
        storage.save_data(bad, path)
    assert path.read_bytes() == before
    assert storage.list_backups(path) == []


def test_validate_rejects_non_dict_and_unknown_top_level_key(valid: dict[str, Any]) -> None:
    """The top level must be an object with exactly the known keys."""
    with pytest.raises(StorageError):
        storage.validate([])
    valid["extra"] = []
    with pytest.raises(StorageError, match="extra"):
        storage.validate(valid)


# 6 -------------------------------------------------------------------------

def test_no_temp_file_left_after_save(path: Path, valid: dict[str, Any]) -> None:
    """Saving leaves no .tmp files behind."""
    storage.save_data(valid, path)
    storage.save_data(valid, path)
    assert list(path.parent.rglob("*.tmp")) == []


def test_failed_replace_keeps_old_file_and_removes_temp(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace fails, the old file is unchanged and the temp file is gone."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    changed = copy.deepcopy(valid)
    changed["memberships"][0]["balance"] = 1

    def broken_replace(source: Any, target: Any) -> None:
        """Simulate a failing replace."""
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        storage.save_data(changed, path)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert list(path.parent.rglob("*.tmp")) == []


# 7 -------------------------------------------------------------------------

def test_first_save_creates_no_backup(path: Path, valid: dict[str, Any]) -> None:
    """There is nothing to back up on the first save."""
    storage.save_data(valid, path)
    assert storage.list_backups(path) == []


def test_second_save_backs_up_previous_content_exactly(
    path: Path, valid: dict[str, Any]
) -> None:
    """The backup holds the exact bytes of the file before the second save."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    valid["memberships"][0]["balance"] = 5
    storage.save_data(valid, path)
    backups = storage.list_backups(path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    assert backups[0].name.startswith("data-") and backups[0].suffix == ".json"


# 8 -------------------------------------------------------------------------

def test_only_20_newest_backups_are_kept(path: Path) -> None:
    """After 25 saves, exactly the 20 newest backups remain, newest first."""
    save_balances(path, 25)
    backups = storage.list_backups(path)
    assert storage.MAX_BACKUPS == 20
    assert len(backups) == 20
    balances = [find(storage.load_data(b), "memberships", "m1")["balance"] for b in backups]
    assert balances == list(range(24, 4, -1))
    assert [b.name for b in backups] == sorted((b.name for b in backups), reverse=True)


# 9 / 10 --------------------------------------------------------------------

def test_restore_skips_broken_backup_and_keeps_broken_main(path: Path) -> None:
    """A broken main and broken newest backup restore the older valid backup."""
    save_balances(path, 3)
    newest, older = storage.list_backups(path)
    path.write_text("garbage main", encoding="utf-8")
    newest.write_text("garbage backup", encoding="utf-8")
    restored = storage.restore_latest_backup(path)
    assert restored == older
    assert balance_of(path) == 1
    corrupt = list(path.parent.glob("data.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "garbage main"


def test_restore_without_valid_backup_raises_and_keeps_main(path: Path) -> None:
    """With no valid backup the main file is left where it is."""
    path.parent.mkdir(parents=True)
    path.write_text("garbage main", encoding="utf-8")
    with pytest.raises(StorageError):
        storage.restore_latest_backup(path)
    assert path.read_text(encoding="utf-8") == "garbage main"
    assert list(path.parent.glob("data.corrupt-*.json")) == []


def test_restore_with_only_invalid_backups_raises_and_keeps_main(path: Path) -> None:
    """Backups that fail validation are not used."""
    save_balances(path, 2)
    for backup in storage.list_backups(path):
        backup.write_text("{}", encoding="utf-8")
    path.write_text("garbage main", encoding="utf-8")
    with pytest.raises(StorageError):
        storage.restore_latest_backup(path)
    assert path.read_text(encoding="utf-8") == "garbage main"


def test_restore_inside_transaction_raises(path: Path) -> None:
    """Restoring inside a transaction would deadlock, so it is refused."""
    with storage.transaction(path):
        with pytest.raises(StorageError):
            storage.restore_latest_backup(path)


# 11 ------------------------------------------------------------------------

def test_load_with_recovery_returns_no_warning_when_fine(
    path: Path, valid: dict[str, Any]
) -> None:
    """A healthy file loads with no warning."""
    storage.save_data(valid, path)
    data, warning = storage.load_with_recovery(path)
    assert data == valid
    assert warning is None


def test_load_with_recovery_restores_and_warns(path: Path) -> None:
    """A broken file is replaced by the newest valid backup and a warning is returned."""
    save_balances(path, 3)
    newest = storage.list_backups(path)[0]
    path.write_text("garbage", encoding="utf-8")
    data, warning = storage.load_with_recovery(path)
    assert find(data, "memberships", "m1")["balance"] == 2
    assert warning is not None
    assert newest.name in warning
    corrupt = list(path.parent.glob("data.corrupt-*.json"))
    assert len(corrupt) == 1 and str(corrupt[0]) in warning
    assert balance_of(path) == 2


def test_load_with_recovery_does_not_undo_a_newer_save(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the file is fine again once the lock is held, no older backup is restored.

    Simulates the race: this session's first read saw a broken file, but another
    session restored and saved a newer version before this one got the lock.
    """
    save_balances(path, 3)
    before = path.read_bytes()
    real_load = storage.load_data
    calls: list[Any] = []

    def stale_first_load(file: Any = None) -> dict[str, Any]:
        """Fail the first read as if it saw the broken file, then read normally."""
        calls.append(file)
        if len(calls) == 1:
            raise StorageError("data.json is not valid JSON")
        return real_load(file)

    monkeypatch.setattr(storage, "load_data", stale_first_load)
    data, warning = storage.load_with_recovery(path)
    assert warning is None
    assert find(data, "memberships", "m1")["balance"] == 3
    assert path.read_bytes() == before
    assert list(path.parent.glob("data.corrupt-*.json")) == []


# Windows: replace while the target is busy ---------------------------------

def save_with_busy_replace(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch, failures: int
) -> list[Any]:
    """Save a change while os.replace fails `failures` times with PermissionError.

    Retrying is switched on and the delay set to 0. Returns the list of replace calls.
    """
    storage.save_data(valid, path)
    monkeypatch.setattr(storage, "RETRY_REPLACE", True)
    monkeypatch.setattr(storage, "REPLACE_ATTEMPTS", 5)
    monkeypatch.setattr(storage, "REPLACE_DELAY_SECONDS", 0)
    real_replace = os.replace
    calls: list[Any] = []

    def busy_replace(source: Any, target: Any) -> None:
        """Raise PermissionError for the first `failures` calls, then replace."""
        calls.append(target)
        if len(calls) <= failures:
            raise PermissionError("file is in use")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", busy_replace)
    changed = copy.deepcopy(valid)
    changed["memberships"][0]["balance"] = 7
    try:
        storage.save_data(changed, path)
    finally:
        monkeypatch.undo()
    return calls


def test_replace_is_retried_while_target_is_busy(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replace that fails a few times with PermissionError still saves."""
    calls = save_with_busy_replace(path, valid, monkeypatch, failures=3)
    assert len(calls) == 4
    assert balance_of(path) == 7
    assert list(path.parent.rglob("*.tmp")) == []


def test_replace_gives_up_and_keeps_old_file(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the target stays busy, the error is raised and the old file is unchanged."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    with pytest.raises(PermissionError):
        save_with_busy_replace(path, valid, monkeypatch, failures=99)
    assert path.read_bytes() == before
    assert list(path.parent.rglob("*.tmp")) == []


def test_replace_is_not_retried_when_retry_is_off(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without retrying (non-Windows), a PermissionError is raised on the first try."""
    storage.save_data(valid, path)
    monkeypatch.setattr(storage, "RETRY_REPLACE", False)
    calls: list[Any] = []

    def denied_replace(source: Any, target: Any) -> None:
        """Always refuse."""
        calls.append(target)
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", denied_replace)
    with pytest.raises(PermissionError):
        storage.save_data(valid, path)
    monkeypatch.undo()
    assert len(calls) == 1


def test_save_succeeds_while_a_reader_briefly_holds_the_file(
    path: Path, valid: dict[str, Any]
) -> None:
    """A real open reader (which blocks os.replace on Windows) does not break a save."""
    storage.save_data(valid, path)
    reader = open(path, "rb")
    timer = threading.Timer(0.2, reader.close)
    timer.start()
    try:
        valid["memberships"][0]["balance"] = 9
        storage.save_data(valid, path)
    finally:
        timer.join()
        reader.close()
    assert balance_of(path) == 9


def test_load_with_recovery_raises_when_restore_fails(path: Path) -> None:
    """If no backup works, the error carries both reasons."""
    path.parent.mkdir(parents=True)
    path.write_text("garbage", encoding="utf-8")
    with pytest.raises(StorageError, match="recovery failed"):
        storage.load_with_recovery(path)


def test_load_with_recovery_on_missing_file_returns_empty(path: Path) -> None:
    """A missing file is a fresh start, not an error."""
    assert storage.load_with_recovery(path) == (storage.empty_data(), None)


# 12 ------------------------------------------------------------------------

def test_transaction_saves_change(path: Path, valid: dict[str, Any]) -> None:
    """Changes made inside the block are saved when it ends."""
    storage.save_data(valid, path)
    with storage.transaction(path) as data:
        find(data, "memberships", "m1")["balance"] = 42
    assert balance_of(path) == 42


def test_transaction_exception_saves_nothing(path: Path, valid: dict[str, Any]) -> None:
    """An exception inside the block means no save and no new backup."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        with storage.transaction(path) as data:
            find(data, "memberships", "m1")["balance"] = 42
            raise RuntimeError("boom")
    assert path.read_bytes() == before
    assert storage.list_backups(path) == []


def test_transaction_invalid_change_is_refused(path: Path, valid: dict[str, Any]) -> None:
    """A change that fails validation raises and leaves the file unchanged."""
    storage.save_data(valid, path)
    before = path.read_bytes()
    with pytest.raises(StorageError):
        with storage.transaction(path) as data:
            find(data, "memberships", "m1")["balance"] = -5
    assert path.read_bytes() == before


def test_transaction_on_missing_file_creates_it(path: Path) -> None:
    """The first transaction on a missing file writes the first file."""
    with storage.transaction(path) as data:
        data["groups"].append({"id": "g1", "name": "G", "admin_id": "u1",
                               "invite_code": "X", "start_balance": 100})
        data["users"].append({"id": "u1", "username": "a", "password_hash": "h"})
        data["memberships"].append({"id": "m1", "user_id": "u1", "group_id": "g1",
                                    "role": "admin", "balance": 100, "restricted": False})
    assert path.exists()


# 13 ------------------------------------------------------------------------

def test_two_threads_do_not_lose_updates(path: Path, valid: dict[str, Any]) -> None:
    """Two threads with 100 increments each raise the balance by exactly 200."""
    valid["memberships"][0]["balance"] = 0
    storage.save_data(valid, path)
    errors: list[BaseException] = []

    def work() -> None:
        """Add 1 to u1's balance 100 times, one transaction each."""
        try:
            for _ in range(100):
                with storage.transaction(path) as data:
                    find(data, "memberships", "m1")["balance"] += 1
        except BaseException as error:  # collected and checked in the main thread
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert balance_of(path) == 200


# 14 ------------------------------------------------------------------------

def test_nested_transaction_raises_and_lock_is_released(path: Path) -> None:
    """A nested transaction raises instead of deadlocking; later ones still work."""
    with storage.transaction(path):
        with pytest.raises(StorageError, match="nested"):
            with storage.transaction(path):
                pass
    with storage.transaction(path):
        pass


# Paths ---------------------------------------------------------------------

def test_data_file_environment_variable_overrides_default(
    path: Path, valid: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """DATA_FILE is used when no path argument is given."""
    monkeypatch.setenv("DATA_FILE", str(path))
    storage.save_data(valid)
    assert path.exists()
    assert storage.load_data() == valid


def test_default_path_is_repo_data_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATA_FILE the default is <repo>/data/data.json, whatever the cwd is."""
    monkeypatch.delenv("DATA_FILE", raising=False)
    expected = Path(storage.__file__).resolve().parent.parent / "data" / "data.json"
    assert storage.DEFAULT_DATA_FILE == expected
    assert storage._resolve_path(None) == expected

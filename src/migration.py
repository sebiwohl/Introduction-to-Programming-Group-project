"""Upgrade stored data from older schema versions to the current one.

Pure functions on the stored dicts, called by storage.py when data is loaded,
so old files, database rows and backups keep working. The upgraded data is
written back the next time anything is saved.

Version 1 -> 2:
* group_id, role and balance move from each user into a membership record;
  a user without a group gets no membership.
* Events get type "yes_no", no line, no result value, and an event date
  equal to their close time.
* New empty lists: memberships, topups, repayments, notifications.

Version 2 -> 3 (members start at 0 and get coins from top-ups):
* Groups lose start_balance.
* Each membership gets "received": what it was given this period. Everyone
  joined with the group's start balance, so that plus every top-up recorded
  in the group, except the old seed script's "seed-topup": it only described
  the start balance and was never added to anyone's balance.
"""

import copy
from typing import Any

MEMBERSHIP_PREFIX = "m_"
# The version 2 seed script stored the start balance as this top-up without
# crediting it, so counting it would count the start balance twice.
OLD_SEED_TOP_UP_ID = "seed-topup"


def membership_id(user_id: str, group_id: str) -> str:
    """Return the id given to a membership made from a version 1 user."""
    return f"{MEMBERSHIP_PREFIX}{user_id}_{group_id}"


def _user_v2(user: dict) -> dict:
    """Return a version 1 user without the fields that moved to memberships."""
    return {"id": user["id"], "username": user["username"],
            "password_hash": user["password_hash"]}


def _membership_v2(user: dict) -> dict:
    """Return the membership for a version 1 user that has a group."""
    return {
        "id": membership_id(user["id"], user["group_id"]),
        "user_id": user["id"], "group_id": user["group_id"],
        "role": user["role"], "balance": user["balance"], "restricted": False,
    }


def _event_v2(event: dict) -> dict:
    """Return a version 1 event with the version 2 fields added."""
    upgraded = dict(event)
    upgraded.update({"type": "yes_no", "line": None,
                     "event_time": event["close_time"], "result_value": None})
    return upgraded


def _upgrade_v1(data: dict) -> dict:
    """Return a version 2 copy of version 1 data (raises KeyError/TypeError if malformed)."""
    upgraded = copy.deepcopy(data)
    users = data["users"]
    upgraded["users"] = [_user_v2(user) for user in users]
    upgraded["memberships"] = [_membership_v2(user) for user in users if user["group_id"]]
    upgraded["events"] = [_event_v2(event) for event in data["events"]]
    upgraded["topups"] = []
    upgraded["repayments"] = []
    upgraded["notifications"] = []
    upgraded["schema_version"] = 2
    return upgraded


def _upgrade_v2(data: dict) -> dict:
    """Return a version 3 copy of version 2 data (raises KeyError/TypeError if malformed)."""
    upgraded = copy.deepcopy(data)
    topped_up: dict = {}
    for topup in data["topups"]:
        if topup["id"] != OLD_SEED_TOP_UP_ID:
            topped_up[topup["group_id"]] = topped_up.get(topup["group_id"], 0) + topup["amount"]
    start = {group["id"]: group["start_balance"] for group in data["groups"]}
    upgraded["groups"] = [{key: value for key, value in group.items() if key != "start_balance"}
                          for group in data["groups"]]
    upgraded["memberships"] = [
        dict(m, received=start[m["group_id"]] + topped_up.get(m["group_id"], 0))
        for m in data["memberships"]
    ]
    upgraded["schema_version"] = 3
    return upgraded


UPGRADES = {1: _upgrade_v1, 2: _upgrade_v2}


def upgrade(data: Any) -> Any:
    """Return the data in the current format; anything not recognised is returned unchanged.

    Each old version is upgraded one step at a time (1 -> 2 -> 3). Malformed old
    data is returned unchanged, so that validation reports the real problem
    instead of a crash here.
    """
    if not isinstance(data, dict):
        return data
    upgraded = data
    try:
        while (type(upgraded.get("schema_version")) is int
               and upgraded["schema_version"] in UPGRADES):
            upgraded = UPGRADES[upgraded["schema_version"]](upgraded)
    except (KeyError, TypeError, AttributeError):
        return data
    return upgraded

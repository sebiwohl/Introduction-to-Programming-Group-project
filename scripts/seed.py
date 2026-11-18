"""Rebuild the demo data: one group, four users and events in every state.

Usage:
    SEED_PASSWORD=<password> python scripts/seed.py              # local file
    SEED_PASSWORD=<password> python scripts/seed.py --database   # DATABASE_URL

By default the data goes to the local file (DATA_FILE, else data/data.json),
even when DATABASE_URL is set. --database writes to the database instead and
asks for confirmation first, because it replaces everything stored there (the
old data is kept as a backup). Never run it against the deployed app without
the team's agreement.

The demo password comes from the SEED_PASSWORD environment variable, so no
password is ever committed. All four demo accounts use it.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auth, service, storage  # noqa: E402
from src.convert import event_to_dict, notification_to_dict, topup_to_dict  # noqa: E402
from src.models import (  # noqa: E402
    TYPE_OVER_UNDER, Event, Notification, TopUp,
)

ADMIN = "demo_admin"
MEMBER = "demo_member"
FRIENDS = ("anna", "ben")
GROUP_NAME = "Demo Friends"
START_BALANCE = Decimal("1000")
PASSWORD_ENV = "SEED_PASSWORD"


def _target(use_database: bool):
    """Return the path argument for storage: a file path, or None for DATABASE_URL."""
    if not use_database:
        return Path(os.environ.get("DATA_FILE") or storage.DEFAULT_DATA_FILE)
    if not os.environ.get(storage.DATABASE_URL_ENV):
        raise SystemExit("--database needs DATABASE_URL to be set.")
    return None


def _confirm_database() -> None:
    """Ask before replacing the database contents; stop unless the answer is 'yes'."""
    answer = input("This replaces ALL data in the database (a backup is kept). Type yes: ")
    if answer.strip().lower() != "yes":
        raise SystemExit("Cancelled, nothing changed.")


def _users(password: str, path) -> dict:
    """Create the demo accounts and the group; return {username: user id}."""
    ids = {name: auth.sign_up(name, password, path).id for name in (ADMIN, MEMBER, *FRIENDS)}
    group = auth.create_group(ids[ADMIN], GROUP_NAME, START_BALANCE, path)
    for name in (MEMBER, *FRIENDS):
        auth.join_group(ids[name], group.invite_code, path)
    return ids


def _add_records(path, ids: dict, now: datetime) -> str:
    """Store the start top-up, an over/under event and a notification; return the event id."""
    with storage.transaction(path) as data:
        group_id = data["groups"][0]["id"]
        topup = TopUp(id="seed-topup", group_id=group_id, amount=START_BALANCE,
                      created_by=ids[ADMIN], created_at=now - timedelta(days=4))
        goals = Event(id="seed-goals", group_id=group_id, creator_id=ids[MEMBER],
                      title="Goals in Sunday's derby", event_type=TYPE_OVER_UNDER,
                      line=Decimal("2.5"), close_time=now + timedelta(days=2),
                      event_time=now + timedelta(days=2, hours=2),
                      description="Over or under 2.5 goals in total.")
        note = Notification(id="seed-note", user_id=ids[ADMIN], group_id=group_id,
                            text="Welcome! Share the invite code with your friends.",
                            created_at=now - timedelta(days=4))
        data["topups"].append(topup_to_dict(topup))
        data["events"].append(event_to_dict(goals))
        data["notifications"].append(notification_to_dict(note))
    return goals.id


def _event(path, user_id: str, title: str, close: datetime, created: datetime) -> str:
    """Create a yes/no event as it would have been created at `created`; return its id."""
    return service.create_event(user_id, title, close, created, path=path).id


def _events(path, ids: dict, now: datetime) -> None:
    """Create events in every state, with bets placed before each one closed."""
    past = now - timedelta(days=3)
    late = _event(path, ids[ADMIN], "Anna is late to dinner", now - timedelta(days=1), past)
    rain = _event(path, ids["ben"], "It rains on Saturday", now - timedelta(hours=2), past)
    film = _event(path, ids["anna"], "Ben finishes the film without sleeping",
                  now + timedelta(hours=2), past)
    gym = _event(path, ids[MEMBER], "Anna goes to the gym 3 times this week",
                 now + timedelta(days=3), past)
    goals = _add_records(path, ids, now)
    bets = [
        (late, MEMBER, "yes", "50"), (late, "ben", "yes", "30"), (late, "anna", "no", "40"),
        (rain, ADMIN, "no", "20"), (rain, "anna", "yes", "60"),
        (film, ADMIN, "yes", "25"), (film, "anna", "no", "15"),
        (gym, "ben", "no", "80"), (gym, ADMIN, "yes", "40"),
        (goals, "anna", "yes", "35"), (goals, "ben", "no", "20"),
    ]
    for event_id, name, side, stake in bets:
        service.place_bet(ids[name], event_id, side, Decimal(stake), past, path)
    service.resolve_event(ids[ADMIN], late, "yes", now, path)
    service.refresh(now, path)  # "It rains" is now closed, waiting for its result


def seed(path, password: str, now: datetime) -> None:
    """Replace the data at `path` with the demo data (None means the database)."""
    storage.save_data(storage.empty_data(), path)
    ids = _users(password, path)
    _events(path, ids, now)


def main() -> None:
    """Parse the arguments, check the password and seed the chosen target."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", action="store_true",
                        help="write to DATABASE_URL instead of the local file")
    args = parser.parse_args()
    password = os.environ.get(PASSWORD_ENV, "")
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Set {PASSWORD_ENV} to a password of at least "
                         f"{auth.MIN_PASSWORD_LENGTH} characters.")
    path = _target(args.database)
    if args.database:
        _confirm_database()
    seed(path, password, datetime.now(timezone.utc))
    print(f"Seeded {'the database' if path is None else path}: "
          f"{GROUP_NAME}, users {', '.join((ADMIN, MEMBER, *FRIENDS))}.")


if __name__ == "__main__":
    main()

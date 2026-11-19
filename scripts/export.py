"""Save a copy of all the app's data to a JSON file (a manual backup).

Usage:
    python scripts/export.py               # the local file (DATA_FILE, else data/data.json)
    python scripts/export.py --database    # the database (read only, changes nothing)
    python scripts/export.py --output PATH # choose where the copy goes

The copy goes to data/exports/export-<UTC time>.json by default. data/ is
git-ignored, which matters: the copy holds every user's password hash, so it
must never be committed to the public repository or shared. The file is only
readable by you, and an existing file is never overwritten.

--database reads DATABASE_URL from the environment, else from the git-ignored
.streamlit/secrets.toml. The connection string is never printed.

The data is loaded the same way the app loads it, so it is checked and, if it
was saved by an older version, upgraded first. Damaged data is reported and
nothing is written. To restore a copy later, load it with storage.load_data and
save it with storage.save_data (the current data is kept as a backup).
"""

import argparse
import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import storage  # noqa: E402

EXPORT_DIR = ROOT / "data" / "exports"
SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"


def _secret_database_url(secrets_file: Path) -> str:
    """Return DATABASE_URL from the secrets file, or '' if it is missing."""
    try:
        with open(secrets_file, "rb") as handle:
            value = tomllib.load(handle).get(storage.DATABASE_URL_ENV, "")
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return value if isinstance(value, str) else ""


def _source(use_database: bool, secrets_file: Path = SECRETS_FILE):
    """Return the storage path argument: a file path, or None for the database."""
    if not use_database:
        return Path(os.environ.get("DATA_FILE") or storage.DEFAULT_DATA_FILE)
    if os.environ.get("DATA_FILE"):
        raise SystemExit("--database cannot be used while DATA_FILE is set.")
    if not os.environ.get(storage.DATABASE_URL_ENV):
        url = _secret_database_url(secrets_file)
        if not url:
            raise SystemExit("--database needs DATABASE_URL (environment or "
                             ".streamlit/secrets.toml).")
        os.environ[storage.DATABASE_URL_ENV] = url
    return None


def default_output(now: datetime, directory: Path = EXPORT_DIR) -> Path:
    """Return the default file name for an export made at `now`."""
    return directory / f"export-{now.astimezone(timezone.utc):%Y%m%d-%H%M%S}.json"


def export(source, output: Path) -> dict:
    """Load the data from `source` and write it to a new file at `output`.

    Returns the number of records per section. Raises StorageError for damaged
    data and FileExistsError if `output` already exists; nothing is written then.
    """
    data = storage.load_data(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return {section: len(data[section]) for section in storage.SECTIONS}


def main() -> None:
    """Parse the arguments, export the data and print a short summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", action="store_true",
                        help="read from DATABASE_URL instead of the local file")
    parser.add_argument("--output", type=Path, help="where to write the copy")
    args = parser.parse_args()
    source = _source(args.database)
    output = args.output or default_output(datetime.now(timezone.utc))
    try:
        counts = export(source, output)
    except storage.StorageError as error:
        raise SystemExit(f"Not exported, the data could not be loaded: {error}") from None
    except FileExistsError:
        raise SystemExit(f"Not exported, {output} already exists.") from None
    summary = ", ".join(f"{count} {section}" for section, count in counts.items())
    print(f"Exported {'the database' if source is None else source} to {output}: {summary}.")
    print("It contains password hashes: keep it private and never commit it.")


if __name__ == "__main__":
    main()

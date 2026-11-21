# Called it!

A betting app for groups of friends: bet virtual coins on everyday events ("Will Tom be late to dinner?"). There is no bookmaker. The odds come from how the group bets (pari-mutuel), and the winners split the whole pool. No real money and no external APIs.

Nova SBE, Fall 2026, Introduction to Programming, group project.

**Live app:** TODO: add the Streamlit Community Cloud URL (`https://<app-name>.streamlit.app`)

## Team

| Name | Student number | GitHub |
|---|---|---|
| TODO | TODO | [sebiwohl](https://github.com/sebiwohl) |
| TODO | TODO | [Ben1212-cloud](https://github.com/Ben1212-cloud) |
| TODO | TODO | [AndreFrazao20](https://github.com/AndreFrazao20) |
| TODO | TODO | [Madalena62587](https://github.com/Madalena62587) |

## What the app does

- **Accounts and groups:** sign up with a username and password, create a group or join one with an invite code. You can be in several groups and switch between them in the top bar.
- **Three kinds of events:** yes/no, names (2 to 10 options, one or several winners) and over/under (a number line set by the creator). Each event has an event date and a time when betting closes.
- **Betting:** stakes are taken from your balance when you bet. The odds update live from the pool. Hot bets, countdowns and "Closing soon" help you find events.
- **Results:** the creator or the group admin sets the result after the event date. Winners split the whole pool in proportion to their stakes. If nobody backed the winner, or only one side has bets, every stake is refunded.
- **Coins and repayment:** everyone starts at 0, and the admin tops up every member by the same amount. The repayment window shows who is up and who is down, and the fewest payments that settle everyone. The real payment happens outside the app, then the admin confirms "Repaid" and a new period starts.
- **Moderation:** members flag fishy events, with an optional note. Enough flags pause betting and open a one-hour poll; the event is deleted only if more than half of the votes say "delete".
- **Overview pages:** Feed, My bets, Leaderboard (with head to head and group records), Group (members, invite code, admin tools) and Profile (avatar and stats), plus a notification bell.

The full rules are in [docs/product-requirements.md](docs/product-requirements.md).

### How the odds work

Pool = all stakes on all sides. Odds for a side = pool / stake on that side. Example: 40 coins on YES and 10 on NO make a pool of 50. The YES odds are 50 / 40 = 1.25 and the NO odds are 50 / 10 = 5.00. If NO wins, a 10-coin NO bet gets 50 back. Money is stored in whole cents, each share is rounded down to the cent, and the leftover cents go to the winner with the biggest stake, so the payouts always add up to the pool.

## Commands

```bash
pip install -r requirements.txt
streamlit run app.py
pytest
```

Needs Python 3.12. Database tests are skipped unless `TEST_DATABASE_URL` points to an empty test database (never the real one).

Demo data (one group, four demo accounts, events in every state) goes into the local file. The demo password is read from `SEED_PASSWORD`, so it is never committed:

```bash
SEED_PASSWORD=<choose-one> python scripts/seed.py
```

In PowerShell: `$env:SEED_PASSWORD="<choose-one>"; python scripts/seed.py`. The demo accounts include `demo_admin` (group admin) and `demo_member`.

`--database` writes to `DATABASE_URL` instead and asks for confirmation, because it replaces everything there. Never run it against the deployed app without the team agreeing.

Manual backup: copy all the data into `data/exports/` (git-ignored). It only reads, so it is safe on the deployed data too. Do it before any change to the stored data format and before the demo:

```bash
python scripts/export.py --database
```

Without `--database` it copies the local file. The copy holds every user's password hash: keep it private, never commit or share it.

## Data storage

Locally the app stores its data in `data/data.json`. The deployed app uses a Supabase (PostgreSQL) database instead, because Streamlit Community Cloud erases local files on every reboot.

Set-up (once, by one team member):

1. Create a free project on supabase.com in a US region (Streamlit Cloud runs in the US). Keep the database password in a password manager.
2. In the project, open **Connect** and copy the **Session pooler** connection string, not the direct one: the direct connection only works over IPv6. Put the password into it.
3. On Streamlit Community Cloud, open the app's **Settings > Secrets** and add `DATABASE_URL = "postgresql://..."`.
4. For local testing against Supabase, put the same line into `.streamlit/secrets.toml`, which is git-ignored. Never commit it or paste it into chats.

Careful: with `DATABASE_URL` in your local `.streamlit/secrets.toml`, `streamlit run app.py` uses the real database, because Streamlit copies secrets into the environment. To work on a local file instead, start the app with `DATA_FILE=data/data.json streamlit run app.py` (a set `DATA_FILE` always wins), or keep `DATABASE_URL` out of your local secrets file.

The tables are created automatically on the first start. See `.env.example` for the variable names.

## How the code is organised

```
app.py              Streamlit entry point: start-up checks, login, page routing
src/
  ui/               Screens only (Feed, My bets, Leaderboard, Group, Profile), CSS in style.py
  service.py        What the screens call: one data load per page, every change in one transaction
  auth.py           Sign-up, login, password hashing and the one shared permission check
  betting.py        Pool, odds, payouts and rounding (pure functions)
  stats.py          Hit rate, streaks, rankings and filters for display (pure functions)
  moderation.py     Flags, polls and deleting events
  balances.py       Top-ups, period results and repayment
  members.py        Restricting, leaving and removing members
  notifications.py  The notification bell and the group activity log
  storage.py        The only module that reads or writes data (JSON file or Supabase)
  migration.py      Upgrades data saved in older formats
scripts/            seed.py (demo data) and export.py (manual backup)
tests/              pytest tests for the logic and the storage
docs/               product-requirements.md: rules, data model, decisions, known limits
```

The business logic does not import Streamlit, so it is tested without the UI.

## Security

- Passwords are stored only as salted PBKDF2-SHA256 hashes. Login errors never say whether the username or the password was wrong.
- Every action goes through one permission check (`auth.require_permission`). Users only see and act on events in their own groups.
- All input is validated in the logic (stakes, titles, dates, options), and text from users is escaped before it is shown.
- No secrets in the repository: `DATABASE_URL` lives in the host's settings or in the git-ignored `.streamlit/secrets.toml`.
- Three dependencies, all pinned in `requirements.txt`.

## Known limits

- There is no background timer: polls end and events close when someone loads a page.
- All data is one JSON row in the database, which suits friend groups but not thousands of users.
- A free Supabase project pauses after a week without activity: open the app before using it.
- Results cannot be undone, and there is no password recovery.

More in section 5.6 of the [requirements](docs/product-requirements.md).

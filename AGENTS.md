# AGENTS.md

Rules for every coding agent working in this repository. Keep this file in the repo and change it only through pull requests.

## Overview
a friends betting app for the Introduction to Programming team project ( 4 students, deadline 2026-11-23 23:59). A group of friends bet virtual coins on everyday events. There is no bookmaker: odds come from how the group bets (pari-mutuel). No real money, no external data or APIs.

Full requirements: `docs/product-requirements.md`. Read it before building or changing a feature. If code and requirements disagree, ask before choosing.

The app must be deployed at a public URL and shown live in the demo, so it is a web app, not a command-line tool.

## Stack
- Python 3.12 with Streamlit (the course default stack). Do not switch stack or mix in another one without asking.
- Storage: JSON files, all access in `src/storage.py` only (see Data). The storage choice may change to SQLite or a hosted database, so no other module may read or write files.
- Tests: `pytest`.
- Add a new dependency only after asking. Pin every version in `requirements.txt`, and keep dependencies minimal.

## Commands
- Install: `pip install -r requirements.txt`
- Run locally: `streamlit run app.py`
- Test: `pytest`
- Reset demo data: `python scripts/seed.py` (rebuilds the same example group, users and events). Never run it against the deployed data without asking.

Update this section if any command changes.

## Structure
- `app.py`: Streamlit entry point and page routing
- `src/models.py`: dataclasses for User, Group, Event, Bet, Poll
- `src/storage.py`: load and save data (atomic writes)
- `src/auth.py`: sign-up, login, password hashing, the shared permission check
- `src/betting.py`: pool, odds, placing bets, closing, resolving, payouts
- `src/moderation.py`: flags and polls
- `src/pages/` or UI helpers: Streamlit screens only, no business logic
- `scripts/seed.py`: demo and test data
- `tests/`: tests
- `docs/`: requirements

Business logic must not import Streamlit, so it can be tested without the UI.

## Product rules to respect
- Pool = all stakes on both sides. Odds for a side = pool / stake on that side. Winner payout = stake x odds of the winning side.
- Odds, pool and leaderboard are calculated, never stored.
- Event states: open, paused, closed, resolved, deleted. Only the allowed actions in the requirements are permitted in each state.
- Winners split the whole pool in proportion to their stakes. If only one side has bets or nobody backed the winner, refund all stakes.
- Money is integer coins (or `Decimal`), never floats. Total paid out must equal the pool exactly. Rounding and where leftovers go is an open decision: ask before implementing.
- Moderation: one flag per member per event, one vote per member per poll. Poll deletes the event only if more than 50% of votes cast are "delete"; a tie keeps the event.
- Poll deadlines are checked when data is loaded or a page opens (the app has no background timer).
- Open decisions are listed in section 6 of the requirements. Do not decide them silently.

## Data
- One module (`src/storage.py`) loads and saves. Save to a temporary file, then replace the real one.
- Detect corrupted or invalid data files at start-up and report them instead of crashing.
- A balance can never go below zero. Stakes are deducted when a bet is placed and refunded or paid out when the event ends.
- Streamlit reruns the script on every interaction and each user gets a separate session. Never keep shared state only in memory or `st.session_state`: save it through the storage module.
- Free hosts may erase local files on restart. Before deployment, confirm the chosen host keeps data, or move storage behind `storage.py` to a database.
- Only one team member changes the data structure at a time. Changes to the stored format go through a pull request that also updates the seed script and `docs/product-requirements.md`.

## Security and permissions
- Never store plain-text passwords. Use salted hashes (`hashlib.pbkdf2_hmac` with `secrets.token_bytes`) and `hmac.compare_digest`. The course guide prefers a provider for login; own username and password is accepted here because the requirements specify it, but do not weaken it.
- Login errors must not reveal whether the username or the password was wrong.
- Check permissions in one shared function in `src/auth.py`, called where the action happens. Hiding a button is not a permission.
- Users can only see and act on events in their own group.
- Resolved events are read-only. No bets on closed, paused or deleted events.
- Validate all input: stake is a positive number within the balance, titles and dates are cleaned, menu choices are checked.
- Each permission and fair-play rule needs a test, including the rejected case.

## Secrets
- The repository is public. Never commit passwords, API keys, tokens or connection strings.
- Read secrets by name from environment variables or `.streamlit/secrets.toml`. The secrets file stays in `.gitignore`; confirm that before the first commit that uses a secret. Commit `.env.example` with placeholders only.
- Never paste real keys into chats, issues or agent conversations.
- If a secret leaks: revoke it at the provider first, then replace it locally and in the host settings.

## Testing
- Cover accepted and rejected actions and edge cases: betting, odds, payouts that sum to the pool, refunds, polls, ties, permissions, invalid input, corrupted data files.
- Tests use temporary data, never the real or deployed data.
- Do not edit, delete or weaken existing tests to make them pass. If a test looks wrong, say so and ask.
- Run `pytest` before every commit. All tests must pass on `main`.

## Conventions
- snake_case for functions and variables
- Add a docstring to every function
- Keep functions short and simple
- Keep UI code and business logic separate
- Do not touch files unrelated to the task

## Workflow
- Work on a branch, never directly on `main`. `main` always holds working, deployed code.
- Small commits with clear messages. Merge small and often; avoid long-lived branches.
- Open a pull request for each change. A second team member reads it before merging: it does what was asked, tests cover it including rejected actions, nothing out of scope or any tests were changed. The merger owns the result.
- Shared files (data structure, `app.py`, `AGENTS.md`) have one owner each; coordinate before editing them.
- Only one agent works in a folder at a time. Commit before switching agents.
- Read every change you accept. Do not claim something works without running it.

## Deployment and demo
- Deploy early and from `main`. Decide the host and record it here. Only committed files and `requirements.txt` reach the host, so check the app runs from a fresh clone.
- Secrets for the deployed app are set in the host settings, not only in a local file.
- The demo is live at the public URL. Slides and videos do not replace it. Keep one demo account per role (admin and member) and starting data from the seed script.
- The `README.md` must contain the deployed URL, names and student numbers of all members, and the commands to run the app and the tests. Keep it in sync with the Commands section.
- Submission: public GitHub repository linked on Moodle by one member, deadline 2026-11-23 23:59. Later commits are not graded.

## Don'ts
- Don't add new dependencies without asking
- Don't switch stack or storage without asking
- Never commit passwords or API keys
- Don't edit tests to make them pass
- Don't decide open requirement questions silently
- Don't run seed or reset scripts against deployed data without asking

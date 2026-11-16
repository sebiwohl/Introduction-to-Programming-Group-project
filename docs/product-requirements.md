# Product Requirements and Ideas

Source: "Programming - Group Project - Called it!" (Google Doc), cross-checked against the earlier `docs/` files (product brief, User Management, Basic feature, data). The Google Doc is a superset of those files, so nothing from them is lost.

## 1. Introduction

### 1.1 Purpose
The Friends Betting App lets a group of friends bet against each other on everyday events, such as "Will Anna be late to dinner?" or "Will it rain on Saturday?". Anyone in the group can create an event, and everyone else bets on it. All bets use virtual coins. No real money is involved.

### 1.2 Core idea: odds come from the group
There is no bookmaker and no external data. The odds are calculated from how the group bets (pari-mutuel betting):

- Pool = all stakes on both sides of an event.
- Odds for a side = pool / total stake on that side.
- Payout for a winner = their stake x odds of the winning side.

The less popular side pays more. Example: four friends stake 10 each on "yes" and one friend stakes 10 on "no". The pool is 50.

| Result     | Who is paid            | Odds           | Payout     |
| ---------- | ---------------------- | -------------- | ---------- |
| "No" wins  | The one "no" bettor    | 50 / 10 = 5.0  | 50         |
| "Yes" wins | The four "yes" bettors | 50 / 40 = 1.25 | 12.50 each |

Odds are based on money staked, not the number of people, and they change every time someone bets.

### 1.3 Goals
- Simple and fast to use for a small friend group.
- Fair: odds are transparent and payouts always add up to the pool.
- Self-moderated: the group can remove events that look fishy.
- Small enough to build in about two weeks.

### 1.4 Out of scope
Real money or payments, external data or APIs.

## 2. User management

### 2.1 Roles
Two roles. The admin also acts as the moderator.

| Action                    | Member                  | Admin      |
| ------------------------- | ----------------------- | ---------- |
| Log in and view the feed  | Yes                     | Yes        |
| Create an event           | Yes                     | Yes        |
| Place a bet               | Yes                     | Yes        |
| Flag an event             | Yes                     | Yes        |
| Vote in a poll            | Yes                     | Yes        |
| Resolve events            | Creator only (decision) | All events |
| Open a poll directly      | No                      | Yes        |
| Delete an event           | Only via poll           | Yes        |
| Invite and remove members | No                      | Yes        |

### 2.2 Groups
- The person who creates a group becomes its admin.
- The admin invites friends. Suggested method: a short invite code that new members enter (decision).
- Each member starts with the same balance, for example 1,000 coins. The admin can set it when creating the group (decision).
- If the admin leaves, they must first hand the role to another member (decision).
- When a member is removed, their open bets are refunded (decision).

### 2.3 Accounts
- A user has a unique username and a password (see section 5).
- A user belongs to one group in the first version. Multiple groups is a stretch goal.

## 3. Features

### 3.1 What the user experiences
1. Log in.
2. Join or create a group.
3. See the event feed with live odds.
4. Create an event, or place a bet on an open one.
5. The event closes and betting is locked.
6. The outcome is resolved and winners are paid out.
7. The leaderboard shows everyone's balance.

### 3.2 Core features (must have)
- **Event feed:** a list of events in the group, showing title, status, close time, the pool, the stake on each side and the current odds.
- **Create event:** title, optional description, two sides (yes / no), and a close time.
- **Place bet:** choose an event, a side and a stake. The stake must be a positive number and cannot exceed the balance, and the event must be open.
- **Live odds:** recalculated after every bet.
- **Close and resolve:** after the close time the event is locked. The outcome is then set and payouts are calculated.
- **Balance and history:** each user sees their balance and their past bets with results.
- **Leaderboard:** members ranked by balance.

### 3.3 Event states

| State    | Meaning                                   | Allowed         |
| -------- | ----------------------------------------- | --------------- |
| Open     | Accepting bets                            | Bet, flag, view |
| Paused   | A poll is running                         | View, vote      |
| Closed   | Past the close time, waiting for a result | View            |
| Resolved | Outcome set, payouts done                 | View            |
| Deleted  | Removed by poll or admin, stakes refunded | None            |

### 3.4 Moderation by poll
- Any member can flag an event they think is fishy. One flag per member per event.
- When a set percentage of members have flagged it (decision, suggested 30% with a minimum of 2 flags), a poll opens automatically and betting on that event is paused.
- The admin can also open a poll directly at any time, without the flag threshold.
- All members can vote "delete" or "keep" in the poll. Each member has one vote.
- The poll runs for a set time (decision, e.g. 24 hours).
- When it ends: if more than 50% of the votes cast are "delete", the event is deleted and every stake is refunded. Otherwise the event stays, the flags are cleared, and betting resumes. A tie means the event stays.

### 3.5 Payout rules
- Winners split the whole pool in proportion to their stakes.
- If nobody bet on the winning side, or only one side has bets, all stakes are refunded (decision).
- Amounts are rounded to 2 decimals, or to whole coins. Where any leftover amount goes needs to be defined (decision).

### 3.6 Stretch features and ideas (only if time allows)
- Personal statistics: win rate, total profit, biggest win.
- Event comments.
- Multiple groups per user.
- Voting on the outcome of an event, in addition to the creator or admin setting it.
- Other types of bets, such as over/under or multiple outcomes. Odds calculation might differ from the yes/no pool model.

## 4. Data

### 4.1 Storage
Data is kept in the JSON format below, loaded at start-up and saved after every change. Only the storage module reads or writes it.

**Where it lives.** The deployed app runs on Streamlit Community Cloud, which erases local files on every reboot (confirmed with a hosting test on 2026-11-16). So when the `DATABASE_URL` environment variable is set, the data is stored in a Supabase (PostgreSQL) database instead: the whole data structure is one JSON row in the table `app_data`, and backups are rows in `app_data_backups` (the newest 20 of each kind are kept). Each change locks the row (`SELECT ... FOR UPDATE`), so two users saving at the same time cannot overwrite each other, and a failed change is rolled back. Both tables have row level security turned on, so Supabase's public web API cannot read them. Without `DATABASE_URL` (local development and tests) the JSON file described next is used.

All data lives in one file, `data/data.json` (the `DATA_FILE` environment variable can point elsewhere), with a top-level `schema_version` (currently 1) next to the `users`, `groups`, `events`, `bets` and `polls` lists. Every save is validated first and then written atomically. Before each save, the previous file is copied to `data/backups/`, and the 20 newest backups are kept. At start-up, `load_with_recovery` restores the newest backup that is still valid if the main file is damaged, moves the broken file aside, and returns a warning that the app shows. Limit: the backups are on the same disk as the data, so a host that wipes local files on restart deletes them as well.

Validation also enforces these rules: every bet on a deleted event has a payout equal to its stake (a full refund). Usernames must be unique, ignoring case and spaces at the start or end ("Anna" and " anna" clash), but each name is stored exactly as entered. `close_time` and `closes_at` must be ISO 8601 times with a timezone offset, for example `2026-11-20T18:00:00+00:00`. Times without an offset are rejected.

### 4.2 Entities

**User**

| Field         | Description                         |
| ------------- | ----------------------------------- |
| id            | Unique identifier                   |
| username      | Unique name                         |
| password_hash | Salted hash, never the plain password |
| group_id      | Group the user belongs to           |
| role          | member or admin                     |
| balance       | Current coins                       |

**Group**

| Field         | Description                |
| ------------- | -------------------------- |
| id            | Unique identifier          |
| name          | Group name                 |
| admin_id      | The admin's user id        |
| invite_code   | Code for joining           |
| start_balance | Coins given to new members |

**Event**

| Field              | Description                                         |
| ------------------ | --------------------------------------------------- |
| id                 | Unique identifier                                   |
| group_id           | Group it belongs to                                 |
| creator_id         | User who created it                                 |
| title, description | Text shown in the feed                              |
| close_time         | When betting locks                                  |
| status             | open, paused, closed, resolved, deleted             |
| outcome            | yes, no, or empty until resolved                    |
| flags              | List of user ids who flagged it                     |

**Bet**

| Field    | Description                          |
| -------- | ------------------------------------ |
| id       | Unique identifier                    |
| event_id | Event bet on                         |
| user_id  | Who placed it                        |
| side     | yes or no                            |
| stake    | Amount staked                        |
| payout   | Amount paid out, empty until resolved |

**Poll**

| Field     | Description                                            |
| --------- | ------------------------------------------------------ |
| id        | Unique identifier                                      |
| event_id  | Event under review                                     |
| opened_by | auto (flags) or the admin's id                         |
| closes_at | When voting ends                                       |
| votes     | Map of user id to delete or keep                       |
| result    | Empty until closed, then deleted or kept               |

### 4.3 Calculated, not stored
Pool, stake per side, odds and the leaderboard are calculated from the bets and balances when needed. This keeps the stored data consistent.

### 4.4 Example
```json
{
  "event": {"id": "e7", "title": "Anna is late to dinner", "status": "open",
            "close_time": "2026-11-20T19:00:00+00:00", "outcome": null, "flags": []},
  "bets": [
    {"user_id": "u1", "side": "yes", "stake": 10},
    {"user_id": "u2", "side": "yes", "stake": 10},
    {"user_id": "u3", "side": "yes", "stake": 10},
    {"user_id": "u4", "side": "yes", "stake": 10},
    {"user_id": "u5", "side": "no",  "stake": 10}
  ]
}
```
Pool 50, "yes" odds 1.25, "no" odds 5.0.

### 4.5 Data rules
- Money has 2 decimals in the app (12.50) and is stored in the data file as whole cents (1250). `src/convert.py` is the only place that converts between the two.
- Times are timezone-aware in the app and stored as ISO 8601 strings with an offset.
- A balance can never go below zero.
- Stakes are deducted when a bet is placed, and refunded or paid out when the event ends.
- The total paid out equals the pool, and refunds equal the stakes.
- Save to a temporary file first and then replace the real one, so a crash cannot corrupt the data.

## 5. Security

This is a learning project with virtual coins, so the goal is sensible basics and clear limits, not full production security.

### 5.1 Authentication
- Passwords are stored only as salted hashes (for example with `hashlib.pbkdf2_hmac`), never in plain text.
- Login attempts are validated, and error messages do not reveal whether the username or the password was wrong.

### 5.2 Authorization
- Every action checks the user's role in one shared function before it runs, for example "only the admin can open a poll directly".
- Users can only see and act on events in their own group.

### 5.3 Fair play
- One flag per member per event, and one vote per member per poll.
- No bets after an event is closed or paused.
- The creator cannot silently change the outcome after it is resolved, so resolved events are read-only.
- Abuse of events is handled by the poll process. Abuse by the admin is a limit of the design, since the admin has full control, and the group should agree to trust them.
- Whether the creator may bet on their own event is open (decision).

### 5.4 Input validation
- Stakes must be positive numbers within the balance.
- Menu inputs, titles and dates are checked and cleaned before use.
- Invalid or corrupted data files are detected at start-up and reported instead of crashing.

### 5.5 Data protection
- Only a username and password hash are stored per user, with no email, real name or payment data.
- Deployed, the data is stored in the team's Supabase database. The connection string is a secret: it is kept in the host's settings and a git-ignored `.streamlit/secrets.toml`, never in the repository.

### 5.6 Known limits
- Anyone with the database connection string (or access to the local data file) could edit the data directly. The app's own rules only protect actions taken through the app.
- A free Supabase project pauses after a week without activity and has no automatic backups; the app keeps its own 20 backups in the database.
- There is no encryption of stored data and no recovery of lost passwords in the first version.

## 6. Open decisions (collected)
- Who resolves events: creator only, or also by group vote (stretch).
- Invite method: short invite code (suggested).
- Starting balance: e.g. 1,000 coins, set by the admin.
- Admin leaving: must hand over the role first.
- Removed member: open bets refunded.
- Flag threshold for auto-poll: suggested 30%, minimum 2 flags.
- Poll duration: e.g. 24 hours.
- No bets on the winning side, or only one side has bets: refund all stakes.
- Rounding (2 decimals or whole coins) and where leftover amounts go.
- May the creator bet on their own event?
- Are other bet types (over/under, multiple outcomes) in scope?

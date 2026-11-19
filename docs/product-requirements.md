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
| Resolve events (after the event date) | Own events  | All events |
| Open a poll directly      | No                      | Yes        |
| Delete an event           | Own events, more than 3 hours before betting closes; otherwise only via poll | Any event, same 3-hour rule |
| Invite and remove members | No                      | Yes (remove only members with no open bets and a result of 0) |
| Restrict a member's betting | No                    | Yes        |
| Top up all balances       | No                      | Yes        |
| Show repayment, press "Repaid" | Show only          | Yes        |
| Hand over the admin role  | No                      | Yes        |
| Leave a group             | Yes (see 2.4)           | Yes, same rules, after choosing a new admin |
| Delete the group          | No                      | Only as the last member |

### 2.2 Groups
- The person who creates a group becomes its admin.
- The admin invites friends with a short invite code that new members enter.
- Every member, the admin included, joins with a balance of 0; there is no starting balance. Once everyone has joined, the admin tops up everyone at once, and then betting can start (see 2.4).
- The admin can hand the role to another member at any time. The admin can leave under the same rules as any member (see 2.4), and must first choose the new admin; the new admin gets a notification. If the admin is the last member, there is nobody to choose, so instead the admin can delete the group.
- The admin can only remove a member who has no open bets and whose result this period is exactly 0: no debt and no winnings (balance equal to the top-ups they received this period, see 2.4).
- If the member still has open bets, the admin restricts their betting first. A restricted member can only view: they cannot bet, create events, flag or vote until the admin lifts the restriction. Once their open bets are resolved, their real result shows. If it is 0, they can be removed straight away; if they have debt or winnings, a repayment is needed first, and after it they can be removed.

### 2.3 Accounts
- A user has a unique username and a password (see section 5).
- A user can belong to several groups. They have a separate balance and role in each group. The feed and "My bets" show all their groups; the leaderboard is per group.

### 2.4 Balances
Decided method: everyone starts at 0, with top-ups and repayments.

- **Joining:** every member joins with a balance of 0, whenever they join. The admin waits until everyone has joined, then tops up everyone at once, and betting can start.
- **Creating events is blocked** for each member whose balance is 0. In a new group that is everyone until the first top-up; later it is only the members who are at 0. They see the note "Top-up required before creating events" until the admin has topped up.
- A balance never goes below 0, so nobody can bet more than they have.
- **Running low:** when a member's balance falls below 10% of the last top-up amount (for example below 50 after a top-up of 500), the admin gets a notification and chooses a top-up or a repayment.
- **Top-up:** only the admin can top up, and chooses the amount each time. Every member of the group always gets the same amount, new members included. There are no top-ups for single members, so the differences between members stay the same.
- **Repayment:** a window shows who is up and who is down compared with what they were given this period (all top-ups they received), with simplified transfers and a copy button for the group chat. Opening and closing the window changes nothing. The real repayment happens outside the app, so no real money goes through the app.
- **Repaid:** only the admin can confirm it, in the repayment window, and must enter a top-up amount above 0 in the same step. All balances go back to 0, a new period starts, and everyone is topped up at once, so betting can continue.
- **Open bets carry over:** bets on events that are not resolved yet stay in place when "Repaid" is confirmed. An open stake is not lost yet, so it counts as the member's in the old period's result and moves into the new period with the bet (it is added to what they received in the new period). Its payout or refund lands in the new period. This way the results of every period add up to 0 and the transfers always match.
- **Stats:** shown for "This period" (since the last repayment) and "All time".
- **Result and debt:** a member's result this period is their balance plus their stakes on open bets, minus the top-ups they received this period. Below 0 they have debt, so they are down. Right after a repayment nobody has debt.
- **Leaving:** possible at any time when the member has no open bets and a result of exactly 0 this period (no debt and no winnings), the same rule as removal, so every repayment still adds up. Someone who is up or down leaves right after the next repayment. Otherwise an error message says why.
- **Joining mid-period:** a member who joins later also starts at 0 and can bet only after the admin's next top-up, which every member gets.

Methods considered and not chosen:

| Method | Why not |
| ------ | ------- |
| Start at 0 and let balances go negative | Members could bet without limit, and desperation bets become likely. A limit and a top-up would still be needed, and someone below 0 could only leave by betting back up, which takes luck. |
| Fixed starting balance with no top-up or repayment (the first version) | Members who run out can no longer play. |

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
- **Event feed:** a list of events in the user's groups, showing title, status, creator, close time, the pool, the stake on each side and the current odds. A timer shows the time left until betting closes; it refreshes every minute and turns red in the last 3 hours. "Closing soon" means betting closes within the next 12 hours.
- **Create event:** title, optional details, a type and an event date, and when betting closes. "Betting closes" options: at the event start, 1 hour before, 1 day before, or custom (a day and an hour field). Betting always closes no later than the event date; there is no "never" option, because betting while the result is already known would give free profits. Types: yes / no, names, or over / under. For over / under the creator sets the line (a number) when creating the event; the line cannot change afterwards, and members bet on "over" or "under" that number. For names (for example "Who is going to show up later?") the creator lists 2 to 10 names: group members picked from a list and/or names typed in (trimmed, not empty, at most 40 characters, no duplicates ignoring case). The names cannot change after the event is created. The creator also chooses "one winner" or "several can win".
- **Place bet:** choose an event, a side (or one name of a names event) and a stake. The stake must be a positive number and cannot exceed the balance, and the event must be open. A member may bet on both sides of the same event (to hedge), and on several names of a names event.
- **Delete event:** the creator or the admin can delete an event up to 3 hours before betting closes. All stakes are refunded. After that, an event can only be removed by flag and poll.
- **Live odds:** recalculated after every bet.
- **Close and resolve:** after the close time the event is locked. After the event date, the creator or the admin (whoever is first) sets the outcome. Before it is saved, a popup asks "Are you sure? Resolving cannot be undone." There is no undo, so nobody can go back to old events later and change results to shift balances. Once the outcome is set, payouts are calculated. For over / under they type in the actual number: above the line means "over" wins, below means "under" wins, and exactly on the line refunds every stake. For names they pick the winning name, or every winning name when several can win.
- **Balance and history:** each user sees their balance and their past bets with results.
- **Leaderboard:** members ranked by balance.
- **Win confetti:** when a result is set, every member who won coins on that event (more paid out than they staked) gets a "You called it" notification. The next time they are on the Feed, confetti falls for 4 seconds with the amount won ("YOU WON +15 COINS"), once per win and on whatever device they use; the notification is then marked read. While the Feed is open it checks for a new win every 30 seconds, so a winner who is already looking at the app sees it without clicking. Losers and refunds get no confetti, and neither do wins from before this feature existed.

### 3.3 Event states

| State    | Meaning                                   | Allowed         |
| -------- | ----------------------------------------- | --------------- |
| Open     | Accepting bets                            | Bet, flag, view; delete by creator or admin more than 3 hours before betting closes |
| Paused   | A poll is running                         | View, vote      |
| Closed   | Past the close time, waiting for a result | View            |
| Resolved | Outcome set, payouts done                 | View            |
| Deleted  | Removed by poll or admin, stakes refunded | None            |

### 3.4 Moderation by poll
- Any member can flag an event they think is fishy. One flag per member per event.
- When 25% of the group's members (at least 2) have flagged it, a poll opens automatically and betting on that event is paused.
- If the flags alone are already more than 50% of the members (for example 2 flags in a group of 3), the event is deleted straight away without a poll, and all stakes are refunded.
- The admin can also open a poll directly at any time, without the flag threshold.
- All members can vote "delete" or "keep" in the poll. Each member has one vote.
- The poll runs 1 hour. If betting closes sooner than that, the poll ends halfway to the close time (for example 30 minutes when 1 hour is left), so it never runs into the deadline.
- When it ends: if more than 50% of the votes cast are "delete", the event is deleted and every stake is refunded. Otherwise the event stays, the flags are cleared, and betting resumes. A tie means the event stays. If nobody votes, the poll closes as "keep" and betting resumes.
- Restricted members (see 2.2) do not count as members for the 25% and 50% flag thresholds.

### 3.5 Payout rules
- Winners split the whole pool in proportion to their stakes.
- If nobody bet on the winning side, or only one side has bets, all stakes are refunded.
- If an over / under result equals the line exactly, all stakes are refunded.
- Names events use the same pool: odds for a name = pool / stake on that name, and its share of the pool is shown as a percentage. The whole pool is split between all bets on the winning name(s) in proportion to their stakes: payout = stake x pool / stake on all winning names. When several can win, a name's odds are the most it can pay, so the feed shows "up to". If nobody bet on a winning name, all stakes are refunded.
- Amounts have 2 decimals. Each winner's share is rounded down to the cent, and the cents left over go to the winner with the biggest stake (on a tie, the bet placed first), so the payouts add up to the pool exactly.

### 3.6 Stretch features and ideas (only if time allows)
- Personal statistics: win rate, total profit, biggest win.
- Event comments.
- Voting on the outcome of an event, in addition to the creator or admin setting it.
- ~~Bets with more than two outcomes.~~ Now a core feature: names events (see 3.2). Over / under is also core and uses the same two-sided pool.

## 4. Data

### 4.1 Storage
Data is kept in the JSON format below, loaded at start-up and saved after every change. Only the storage module reads or writes it.

**Where it lives.** The deployed app runs on Streamlit Community Cloud, which erases local files on every reboot (confirmed with a hosting test on 2026-11-16). So when the `DATABASE_URL` environment variable is set, the data is stored in a Supabase (PostgreSQL) database instead: the whole data structure is one JSON row in the table `app_data`, and backups are rows in `app_data_backups` (the newest 20 of each kind are kept). Each change locks the row (`SELECT ... FOR UPDATE`), so two users saving at the same time cannot overwrite each other, and a failed change is rolled back. Both tables have row level security turned on, so Supabase's public web API cannot read them. Without `DATABASE_URL` (local development and tests) the JSON file described next is used.

All data lives in one file, `data/data.json` (the `DATA_FILE` environment variable can point elsewhere), with a top-level `schema_version` (currently 3) next to the `users`, `memberships`, `groups`, `events`, `bets`, `polls`, `topups`, `repayments` and `notifications` lists. Every save is validated first and then written atomically. Before each save, the previous file is copied to `data/backups/`, and the 20 newest backups are kept. At start-up, `load_with_recovery` restores the newest backup that is still valid if the main file is damaged, moves the broken file aside, and returns a warning that the app shows. Limit: the backups are on the same disk as the data, so a host that wipes local files on restart deletes them as well.

Validation also enforces these rules: every bet on a deleted event has a payout equal to its stake (a full refund). Usernames must be unique, ignoring case and spaces at the start or end ("Anna" and " anna" clash), but each name is stored exactly as entered. `close_time` and `closes_at` must be ISO 8601 times with a timezone offset, for example `2026-11-20T18:00:00+00:00`. Times without an offset are rejected. A user has at most one membership per group. An over/under event must have a line and a yes/no event must not; only over/under events have a result number, set exactly when they are resolved, and their outcome must follow from it (above the line yes = over, below no = under, exactly on the line no outcome and every stake refunded); betting must close no later than the event date (`close_time` <= `event_time`). A names event (`"type": "names"`) has no line and no result number; it must have 2 to 10 valid options, every bet on it must name one of its option ids, its `outcome` stays null, and its `winners` are set exactly when it is resolved (only one unless several can win). The names fields (`options`, `multi_winner`, `winners`) are stored only on names events, so other events keep exactly the format above.

**Older data.** Data saved in an older format is upgraded when it is loaded (`src/migration.py`), one version at a time, and saved in the new format at the next change, so existing files, database rows and backups keep working without a separate step.
- Version 1 → 2: group, role and balance move from the user into a membership.
- Version 2 → 3: groups lose `start_balance`, and each membership gets `received` = the group's old start balance plus every top-up recorded in the group (everyone had joined with the start balance).

Names events need no upgrade: they only add optional fields to version 2.

### 4.2 Entities

**User**

| Field         | Description                         |
| ------------- | ----------------------------------- |
| id            | Unique identifier                   |
| username      | Unique name                         |
| password_hash | Salted hash, never the plain password |
| avatar        | Optional profile picture: fox, panda, cat, dog, frog, owl or rabbit (left out until the user picks one) |

**Membership** (a user's place in one group)

| Field      | Description                                        |
| ---------- | -------------------------------------------------- |
| id         | Unique identifier                                  |
| user_id    | The member                                         |
| group_id   | The group                                          |
| role       | member or admin                                    |
| balance    | Current coins in this group                        |
| restricted | true when the admin has restricted the member (2.2) |
| received   | Coins given by top-ups this period, since the last repayment, plus the open stakes carried over at the last repayment (2.4). Result this period = balance + open stakes - received; below 0 means debt |

**Group**

| Field         | Description                |
| ------------- | -------------------------- |
| id            | Unique identifier          |
| name          | Group name                 |
| admin_id      | The admin's user id        |
| invite_code   | Code for joining           |

There is no starting balance: members join with 0 and get coins from top-ups (2.4).

**Event**

| Field              | Description                                         |
| ------------------ | --------------------------------------------------- |
| id                 | Unique identifier                                   |
| group_id           | Group it belongs to                                 |
| creator_id         | User who created it                                 |
| title, description | Text shown in the feed                              |
| close_time         | When betting locks                                  |
| status             | open, paused, closed, resolved, deleted             |
| outcome            | yes, no, or empty until resolved (over/under: yes = over, no = under; always empty for names events) |
| flags              | List of user ids who flagged it                     |
| type               | yes_no, over_under or names                         |
| line               | Over/under line as decimal text ("2.5"), empty for yes/no and names |
| event_time         | When the event happens; betting closes no later     |
| result_value       | Over/under: the actual number entered when resolving, as decimal text |
| options            | Only on names events: 2 to 10 `{id, name}` objects, ids like `o1` |
| multi_winner       | Only on names events: true if several names can win |
| winners            | Only on names events: ids of the winning options, empty until resolved |

**Bet**

| Field    | Description                          |
| -------- | ------------------------------------ |
| id       | Unique identifier                    |
| event_id | Event bet on                         |
| user_id  | Who placed it                        |
| side     | yes or no, or an option id of a names event |
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

**TopUp**

| Field      | Description                              |
| ---------- | ---------------------------------------- |
| id         | Unique identifier                        |
| group_id   | Group whose members all got this amount  |
| amount     | Coins per member                         |
| created_by | The admin                                |
| created_at | When it happened                         |

**Repayment** (the admin confirmed "Repaid"; a new period starts)

| Field      | Description       |
| ---------- | ----------------- |
| id         | Unique identifier |
| group_id   | The group         |
| created_by | The admin         |
| created_at | When it happened  |

**Notification**

| Field      | Description                          |
| ---------- | ------------------------------------ |
| id         | Unique identifier                    |
| user_id    | Who it is for                        |
| group_id   | The group it is about, or empty      |
| text       | The message                          |
| created_at | When it was created                  |
| read       | true once the user has seen it       |

Win notifications (for the confetti) have the id `win:<event id>:<user id>` and become read once the confetti has been shown.

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
- No bets after an event is closed or paused. Betting on both sides of an event is allowed.
- The creator cannot silently change the outcome after it is resolved, so resolved events are read-only.
- Abuse of events is handled by the poll process. Abuse by the admin is a limit of the design, since the admin has full control, and the group should agree to trust them.
- The creator may bet on their own event. The group can flag the event if this looks unfair.

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
- An event that the creator and the admin never resolve stays unresolved: its stakes stay locked, and members with bets on it cannot leave or be removed until it is resolved.
- A wrong result cannot be corrected, because resolving cannot be undone. The group has to trust the creator and the admin, as with other admin actions (5.3).

## 6. Decisions

### 6.1 Decided
- Who resolves events: the creator or the admin, whoever is first, after the event date.
- "Betting closes" options: at the event start, 1 hour before, 1 day before, or custom (day and hour). No "never" option; betting closes no later than the event date.
- Invite method: short invite code.
- Starting balance: none; everyone starts at 0, also after each repayment.
- Balances: start at 0, with top-ups and repayments (section 2.4).
- Top-up amount: chosen by the admin each time.
- Repaid: confirmed by the admin in the repayment window, together with a top-up amount above 0 (any amount; it does not need to cover open stakes, since stakes are taken when a bet is placed); balances go back to 0 and everyone is topped up in the same step. Open bets carry over into the new period, and their stakes count as not lost yet, so every period's results add up to 0. The admin ticks "Everyone has paid" before pressing "Repaid".
- Creating events: blocked for each member whose balance is 0 (in a new group, everyone until the first top-up).
- Low balance: below 10% of the last top-up amount; the admin is notified.
- Rounding: 2 decimals, each share rounded down to the cent; leftover cents go to the winner with the biggest stake (on a tie, the bet placed first).
- The creator may bet on their own event.
- Admin leaving: same rules as any member, and the admin chooses the new admin first; the new admin is notified. The admin can also hand the role over at any time.
- Leaving a group: at any time with no open bets and a result of exactly 0 (no debt, no winnings), like removal; otherwise an error message. Leaving with winnings is not allowed, because the others' debts would then be owed to nobody.
- Joining: every member, the admin included, joins with 0. The note "Top-up required before creating events" is shown until the admin tops up everyone at once; then betting starts. Members who join later also start at 0 and wait for the next top-up.
- Top-ups: always the same amount for every member; no top-ups for single members.
- Removing a member: only with no open bets and a result of exactly 0 (no debt, no winnings). If they still have open bets, the admin restricts their betting first; once their open bets are resolved, they are removed straight away if their result is 0, or after the next repayment if they have debt or winnings.
- Restricted member: can only view; cannot bet, create events, flag or vote, and does not count for the flag thresholds.
- Admin as the last member: can delete the group instead of choosing a new admin.
- Poll with no votes: closes as "keep" and betting resumes.
- Resolving: a popup asks "Are you sure? Resolving cannot be undone." There is no undo.
- Unresolved events: stay unresolved, with no automatic refund (a known limit, 5.6).
- Multiple groups per user: in scope, with a balance and role per group.
- Flag threshold for auto-poll: 25% of members, minimum 2 flags. If the flags are already more than 50% of the members, the event is deleted without a poll.
- Poll duration: 1 hour, or halfway to the betting close if that comes sooner.
- Deleting an event: creator or admin, only more than 3 hours before betting closes, stakes refunded.
- No bets on the winning side, or only one side has bets: refund all stakes.
- Names events (bets on one of several names): in scope; 2 to 10 names, fixed once the event is created; one winner or several can win; the pool is split between the bets on the winning names, and if nobody backed a winner, all stakes are refunded.
- Over / under bets: in scope; the creator sets the line when creating the event, and it cannot change. To resolve, the creator or admin types in the actual number; a result exactly on the line refunds every stake.
- Betting on both sides of one event: allowed.
- Pressing "Repaid": admin only.
- Timer: refreshes every minute; red in the last 3 hours. "Closing soon" tab: betting closes within the next 12 hours.

### 6.2 Still open
- Nothing open at the moment.

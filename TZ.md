# TZ — Stand Manager Bot (hackathon P0)

## 1. Product

Create one Telegram bot for managing shared development/test stands.
The same bot can be added to any number of group chats. Each chat is an independent workspace with its own Teams, roles, users and Stands.

The bot must use Python + aiogram + PostgreSQL.

## 2. Core entities

### User
Global Telegram identity.
Key: `telegram_user_id`.
Username/display name are mutable presentation fields only.

### Workspace
One Telegram group/supergroup.
Key: `telegram_chat_id`.
One bot process serves many Workspaces.

### WorkspaceMember
Membership/role of User in a Workspace.
Roles:
- USER
- MODERATOR
- ADMIN

Roles are local to Workspace, not global.

### Team
Development team inside one Workspace.
One Workspace can contain many Teams.
A User can belong to many Teams.
Team has a unique case-insensitive `slug` inside its Workspace.

### TeamMember
Many-to-many relation User <-> Team.

### Stand
A stand belongs to exactly one Team.
A stand is free when `occupied_by_user_id IS NULL`; otherwise it is occupied by that User.
Stand name is unique case-insensitively inside Team.

## 3. Chat onboarding

When bot is added to a new group/supergroup:
1. upsert Workspace by `telegram_chat_id`;
2. obtain available Telegram chat administrators;
3. create/update their User + WorkspaceMember records;
4. Telegram owner receives ADMIN;
5. Telegram administrators receive MODERATOR;
6. send welcome message with `/help` and next step.

Do not promise full import of every existing chat member. Telegram Bot API does not reliably expose complete member enumeration.

Other users are added to the local DB when:
- they interact with the bot / send a handled command;
- they are the author of a message used by ADMIN/MODERATOR as Reply target;
- membership events are available to the bot.

## 4. Permissions

### USER
Can:
- see Teams and own Team's stands;
- take a free Stand in a Team they belong to;
- release their own Stand.

Cannot:
- create/delete Team;
- create/remove Stand;
- add/remove Team users;
- release somebody else's Stand.

### MODERATOR
USER permissions plus:
- add/remove users from Team;
- create/remove free Stands;
- take a free Stand for another user using Reply;
- release another user's Stand.

Cannot create/delete Team.

### ADMIN
MODERATOR permissions plus:
- create Team;
- delete Team.

For P0, roles come from Telegram owner/admin bootstrap. `/set_role` is optional P1.

## 5. Commands

### Common

```text
/start
/help
/teams
/users
/team_users <team>
/stands <team>
/take_stand <team> <stand>
/untake_stand <team> <stand>
```

### MODERATOR / ADMIN

```text
/add_user <team>       # command must be a Reply to target user's message
/remove_user <team>    # Reply to target user's message
/create_stand <team> <stand>
/remove_stand <team> <stand>
```

### ADMIN

```text
/create_team <slug> [display name]
/delete_team <team>
```

## 6. Command behavior

### `/teams`
Show Teams of current Workspace.

### `/users`
Show users known to the bot in current Workspace and their local role.
Do not claim this is necessarily the complete Telegram member list.

### `/create_team backend Backend Team`
ADMIN only.
Create Team in current Workspace.
Reject duplicate slug ignoring case.

### `/delete_team backend`
ADMIN only.
Reject deletion if any Stand in Team is occupied.

### `/add_user backend`
MODERATOR/ADMIN only and used as Reply.
Use replied message's `from_user` as target.
Upsert target User/WorkspaceMember, then create TeamMember.
Repeated add is idempotent.

### `/remove_user backend`
MODERATOR/ADMIN only and used as Reply.
Reject if target owns any occupied Stand in Team.
Remove only TeamMember, not global User.

### `/create_stand backend dev-1`
MODERATOR/ADMIN only.
Create free Stand in Team.
Reject duplicate name ignoring case.

### `/remove_stand backend dev-1`
MODERATOR/ADMIN only.
Only free Stand may be removed.

### `/stands backend`
Show every Stand and current owner, for example:

```text
Team: Backend [backend]
🟢 dev-1 — свободен
🔴 dev-2 — Иван (@ivan), с 15:42
Свободно: 1 / 2
```

### `/take_stand backend dev-1`
Caller must belong to Team.
If no Reply, target is caller.
If MODERATOR/ADMIN uses the command as Reply, target may be replied user; target must belong to Team.

If free: occupy atomically.
If already owned by caller/target: friendly idempotent response.
If owned by somebody else: tell who owns it.

Two concurrent requests must never both succeed.

### `/untake_stand backend dev-1`
USER may release only their own Stand.
MODERATOR/ADMIN may release another user's Stand.
Repeated release of already free Stand is idempotent.

## 7. Required isolation

Every operation is scoped by the current Telegram `chat_id` -> Workspace.

Never resolve:
- Team by slug globally;
- Stand by name globally;
- role globally.

A user may be ADMIN in one Workspace and USER in another.
Team `backend` may exist independently in many Workspaces.

## 8. Persistence and concurrency

PostgreSQL is the source of truth.
Restarting the Python process must not lose Teams, membership or Stand ownership.

Atomic stand take should be implemented with a conditional `UPDATE ... WHERE occupied_by_user_id IS NULL RETURNING ...` or equivalent row-safe transaction.

## 9. P0 schema

Minimum tables and fields:

```text
users
  id, telegram_user_id UNIQUE, username NULL, display_name, created_at, updated_at

workspaces
  id, telegram_chat_id UNIQUE, title NULL, created_at, updated_at

workspace_members
  workspace_id, user_id, role, created_at, updated_at
  UNIQUE(workspace_id, user_id)

teams
  id, workspace_id, slug, name, created_by_user_id, created_at, updated_at
  UNIQUE(workspace_id, normalized slug)

team_members
  team_id, user_id, created_at
  UNIQUE(team_id, user_id)

stands
  id, team_id, name, occupied_by_user_id NULL, occupied_at NULL,
  created_by_user_id, created_at, updated_at
  UNIQUE(team_id, normalized name)
```

Use UUIDs or integer internal PKs consistently; Telegram IDs must be stored as BIGINT.
No audit table is required for the first working demo.

## 10. Demo acceptance flow

From a real Telegram group:

1. add bot -> welcome appears;
2. Telegram owner/admin can administer the Workspace;
3. `/create_team backend Backend`;
4. `/create_team mobile Mobile`;
5. moderator replies to User A: `/add_user backend`;
6. moderator replies to User B: `/add_user backend`;
7. `/create_stand backend dev-1`;
8. `/create_stand backend dev-2`;
9. User A: `/take_stand backend dev-1` -> success;
10. User B: `/take_stand backend dev-1` -> bot says User A owns it;
11. User B: `/take_stand backend dev-2` -> success;
12. `/stands backend` shows both owners;
13. moderator `/untake_stand backend dev-1` -> releases A's stand;
14. `/stands mobile` shows independent Team state;
15. restart bot process; `/stands backend` still shows persisted state.

P0 is accepted when this flow works reliably.

## 11. Explicitly out of P0

Do not implement until the flow above works:
- FastAPI;
- Redis/queues;
- webhook hosting;
- Mini App/web frontend;
- OpenRouter/LLM features;
- TTL auto-release;
- notifications;
- usage history/audit log;
- team-specific roles beyond Workspace roles;
- complex role editor;
- elaborate dashboards;
- production deployment.

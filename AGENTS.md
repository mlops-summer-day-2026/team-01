# team-01 — Codex instructions

## Goal

Build a **working Telegram bot for managing shared development/test stands**.
This is a 60-minute hackathon build. Optimize for a reliable live demo, not production completeness.

Customer pain: developers sharing a limited pool of stands waste time asking who owns which stand and accidentally collide over the same resource.

**Demo is done when, from real Telegram phones, we can:**
1. add the same bot to a group and have it initialize that chat automatically;
2. create two Teams in the same chat;
3. add users to a Team;
4. create stands;
5. let user A take a stand;
6. show user B that the same stand is already owned by A;
7. show `/stands <team>`;
8. let a moderator/admin release a stand for another user;
9. restart the Python process and keep state in PostgreSQL.

## Source of truth

Read `TZ.md` before coding. It contains the current functional scope.

Priority on conflicts:
1. latest explicit user instruction;
2. `TZ.md`;
3. this `AGENTS.md`;
4. `README.md` event defaults.

The README's static `index.html`/GitHub Pages path is **not** the product for this task. The live Telegram bot is the demo. Preserve all README Git/secrets rules.

## Time budget and priorities

P0 must work end-to-end before anything else.

Implement in this order:
1. config + PostgreSQL connection;
2. DB models/schema;
3. automatic Workspace/User bootstrap;
4. Team + user membership commands;
5. Stand create/list/take/release;
6. RBAC and moderator override;
7. concurrency check for `take_stand`;
8. restart smoke test;
9. only then UX polish/tests/P1.

Do **not** spend P0 time on:
- FastAPI or a web frontend;
- OpenRouter/LLMs;
- Redis, Celery, Kafka, RabbitMQ;
- webhook deployment;
- Telegram Mini App;
- full audit history;
- Alembic unless P0 is already working;
- elaborate repository/service abstractions;
- features not required by `TZ.md`.

Prefer the smallest implementation that is clear and testable.

## Stack

Use:
- Python 3.12+
- aiogram 3.x
- PostgreSQL
- SQLAlchemy 2.x async
- asyncpg
- long polling
- Docker Compose for local PostgreSQL
- `python-dotenv` or `pydantic-settings` for env config; choose one, not both

For the hackathon, creating tables on startup with SQLAlchemy metadata is acceptable. Do not block the demo on migrations.

Required environment variables:
- `TELEGRAM_BOT_TOKEN`
- `DATABASE_URL`

Secrets only from `.env`. Never hard-code, print, commit, or paste real tokens into source.

## Architecture invariants

Keep these rules even if implementation is simplified:

### Multi-chat isolation

One bot serves many Telegram chats.
Each Telegram chat maps to one `Workspace` by `telegram_chat_id`.
Every Team is scoped to exactly one Workspace.
Never query a Team only by slug or a Stand only by name without checking the current Workspace.

### Identity and roles

`User` is global and identified by Telegram `user_id`, never by username.
Roles are **per Workspace**, not global:
- USER
- MODERATOR
- ADMIN

Use a `WorkspaceMember` relation for role/membership in a chat.
The same Telegram user may be ADMIN in chat A and USER in chat B.

### Initial chat bootstrap

When the bot is added to a group/supergroup:
- upsert the Workspace from `telegram_chat_id`;
- fetch known Telegram chat administrators;
- map Telegram owner -> ADMIN;
- map Telegram administrators -> MODERATOR;
- send a short welcome/help message.

Do not claim to fetch every pre-existing group member: Bot API cannot reliably enumerate all members.
Other users are discovered when they interact with the bot or are added manually by moderator/admin via Reply.

### Team membership

A User can belong to multiple Teams.
ADMIN/MODERATOR can add a user to a Team using Reply:

`/add_user <team>`

Use `reply_to_message.from_user.id` as the target identity.
Do not depend on `@username` lookup.

### Stand ownership

A Stand belongs to one Team.
Free stand: `occupied_by_user_id IS NULL`.
Do not keep a second independent FREE/BUSY status field.
A normal USER can release only a stand owned by themselves.
MODERATOR/ADMIN may release another user's stand and may take a free stand for the replied-to user.

### Atomic take

`/take_stand` must be race-safe. Do not implement it as unprotected `SELECT -> UPDATE`.
Use one conditional PostgreSQL update equivalent to:

```sql
UPDATE stands
SET occupied_by_user_id = :user_id,
    occupied_at = NOW(),
    updated_at = NOW()
WHERE id = :stand_id
  AND occupied_by_user_id IS NULL
RETURNING id;
```

Exactly one concurrent caller may succeed.

## P0 commands

Implement these first:

```text
/start
/help
/teams
/users
/create_team <slug> [display name]
/delete_team <team>
/add_user <team>            # MODERATOR/ADMIN, used as Reply
/remove_user <team>         # MODERATOR/ADMIN, used as Reply
/team_users <team>
/create_stand <team> <stand>
/remove_stand <team> <stand>
/stands <team>
/take_stand <team> <stand>
/untake_stand <team> <stand>
```

For MODERATOR/ADMIN, `/take_stand` and `/untake_stand` may target the author of the replied-to message. Without Reply they target the caller.

Do not implement `/set_role` until P0 works. Initial roles from Telegram chat admin status are enough for the demo.

## Minimal data model

Keep the schema small:

```text
users
workspaces
workspace_members
teams
team_members
stands
```

Required relations:

```text
User N <-> N Workspace  via workspace_members (role here)
Workspace 1 -> N Team
User N <-> N Team       via team_members
Team 1 -> N Stand
```

Case-insensitive uniqueness:
- Team slug inside Workspace
- Stand name inside Team

Persist all product state in PostgreSQL; do not use in-memory state as the source of truth.

## Expected corner cases

Handle these explicitly:
- bot added to a new chat -> create Workspace;
- same bot added to many chats -> data remains isolated;
- bot reuses an existing `telegram_chat_id` -> reuse its Workspace;
- user has no username -> use display name;
- username changes -> update display fields, identity stays Telegram user_id;
- unknown Team/Stand -> friendly error;
- duplicate Team/Stand ignoring case -> reject;
- user not in Team -> cannot take its stand;
- remove user who owns Team stands -> reject until released;
- remove occupied stand -> reject;
- delete Team with occupied stands -> reject;
- repeated take by current owner -> idempotent friendly response;
- repeated release of free stand -> idempotent friendly response;
- concurrent take -> one winner only;
- USER cannot release somebody else's stand;
- MODERATOR/ADMIN can release for another user.

## File ownership and agent behavior

Codex may create/edit the implementation files it needs under `app/`, `tests/`, dependency files and `docker-compose.yml`.
Do not rewrite `README.md`, `.githooks/`, `CLAUDE.md`, `AGENTS.md` or `TZ.md` unless the user explicitly asks.
If teammates have claimed files, respect that ownership.
For small ambiguities, choose the simplest interpretation consistent with `TZ.md` and keep building; do not block P0 on optional clarification.
After each meaningful milestone, give a short status line with what now works.

## Implementation style

Time is limited:
- prefer 4–8 small modules over a deep package tree;
- keep handlers thin where practical, but do not build abstraction layers only for architecture purity;
- use type hints for public functions and DB models;
- return clear Russian Telegram messages;
- keep commands and behavior deterministic;
- no premature caching;
- no background workers unless required by P0;
- no unrelated refactors.

A reasonable structure is:

```text
app/
  main.py
  config.py
  db.py
  models.py
  services.py
  handlers.py

tests/
docker-compose.yml
requirements.txt or pyproject.toml
.env.example
```

Use whichever dependency file is fastest; do not maintain both.

## Verification

Before saying P0 is complete, run at least:

1. Python import/startup check;
2. DB schema creation against PostgreSQL;
3. focused tests or a small script for atomic `take_stand`;
4. manual Telegram smoke flow from `TZ.md`;
5. process restart and `/stands` check;
6. `git diff --check` and inspect `git diff`.

If a test cannot be run, say exactly which one and why. Do not claim unverified behavior.

## Git and shared-repo safety

Follow the hackathon README:
- work in `main` unless the team explicitly says otherwise;
- commit/checkpoint frequently when asked to manage Git;
- before push: `git pull --rebase`;
- never `git push --force`;
- never `git reset --hard` without explicit team approval;
- never commit `.env`, tokens, passwords or `SECRET.md`;
- do not edit `.githooks/` unless explicitly asked;
- do not overwrite unrelated teammate work.

If another teammate owns a file, do not silently rewrite it.

## Stop condition

Once the end-to-end demo works, **stop adding architecture**.
Spend remaining time on:
- clear bot messages;
- `/help`;
- demo reliability;
- one concurrency test;
- README run instructions if missing.

A working demo beats an incomplete "production-ready" design.

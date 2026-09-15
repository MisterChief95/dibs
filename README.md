# dibs

An agent skill for coordinating multiple local coding agents that share one workspace. Dibs is the shared source of truth: it tracks tasks, dependencies, ownership leases, and file/resource reservations in a local SQLite database, so parallel agents don't clobber each other's edits or duplicate work.

Dibs itself never launches, stops, or messages agents — it's a coordination ledger that agents read from and write to.

## Why

Running several coding agents on the same checkout is easy to break: two agents edit the same file, one starts work whose dependency isn't finished yet, or a crashed agent leaves work silently unclaimed. Dibs solves this with:

- **Dependency-aware tasks** — a task only becomes claimable once everything it depends on is `done`.
- **Expiring ownership leases** — claiming a task grants a time-limited, token-protected lease; leases must be renewed with a heartbeat or they expire and the task becomes reclaimable.
- **Path and resource reservations** — an agent reserves the specific files, directory trees, or named resources (e.g. `git-index`) it's about to touch, so conflicting claims are rejected up front instead of discovered as a merge conflict.
- **Handoffs and an audit log** — every claim, note, heartbeat, and status change is recorded, so any agent (or human) can reconstruct what happened and why.
- **Searchable metadata** — tasks carry created/updated timestamps, an optional type, and tags, with indexed filters for large boards.

## How it works

`skills/dibs/scripts/dibs.py` is a single dependency-free Python script that is the entire implementation. It's driven by a `SKILL.md` written for LLM agents, describing the coordination contract (claim → heartbeat → reserve/release → handoff → complete/block/cancel/review) and exact command usage. See [skills/dibs/SKILL.md](skills/dibs/SKILL.md) for the full contract.

State lives in a per-workspace SQLite database (default `WORKSPACE/.dibs/tasks.sqlite3`), so each coordinated project gets its own isolated task board.

## Installation

Dibs ships as a plugin/skill you install once per assistant, then use in any project by pointing `--workspace` at that project's directory.

**Claude Code**

```
/plugin marketplace add MisterChief95/dibs
/plugin install dibs@dibs
```

This installs the coordination skill (so Claude knows the claim/heartbeat/handoff contract in any project) and the four read-only `/dibs:list`, `/dibs:show`, `/dibs:next`, `/dibs:events` commands.

**GitHub Copilot**

Copilot has no cross-repo plugin installer — its instructions are per-repository. Copy [`skills/dibs/`](skills/dibs), [`.github/copilot-instructions.md`](.github/copilot-instructions.md), and [`.github/prompts/`](.github/prompts) into the target repo. VS Code Copilot Chat then exposes `/dibs-list`, `/dibs-show`, `/dibs-next`, and `/dibs-events`, and Copilot gets pointed at the coordination contract automatically.

**Codex**

```
codex plugin add MisterChief95/dibs
```

Installs the coordination skill via [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json). Codex doesn't yet get dedicated `/dibs:*` commands here — it reads `SKILL.md` directly when coordination is relevant.

In every case, the actual logic is the one script, [`skills/dibs/scripts/dibs.py`](skills/dibs/scripts/dibs.py) — nothing to build, no server, no third-party dependencies.

## Use cases

- Fan out a plan across multiple agent workers (e.g. subagents or separate CLI sessions) working the same repo concurrently.
- Prevent two agents from editing the same file or directory tree at the same time.
- Recover cleanly when a worker crashes or is killed mid-task, without losing its reservations to a race.
- Give a human or a coordinating agent visibility into what's claimed, what's blocked, and what's ready to pick up next.

## For agents: the worker lifecycle

1. `init` the database once, then `import` a JSON task plan (ID, title, priority, dependencies, work areas, description, acceptance criteria, plus optional `type` and `tags`).
2. Workers call `next` / `show` to find ready work, then `claim` or `claim-next` to take a task and reserve the paths/resources it needs.
3. While working: `heartbeat` to renew the lease, `reserve`/`release` to adjust scope, `note` for progress updates, `handoff` to record context without releasing ownership.
4. To finish: `review`, `complete`, `block`, or `cancel` — each releases the lease and any remaining reservations.

Full command reference, conflict handling, and exit codes are documented in [SKILL.md](skills/dibs/SKILL.md).

## Task metadata and filtering

Tasks record `created` and `updated` timestamps and may include a lowercase `type` and `tags`:

```json
{
  "type": "feature",
  "tags": ["database", "backend"]
}
```

Tags are ephemeral board metadata. Anyone can add or remove them without claiming the task or changing its revision; the change still refreshes `updated` and records an audit event:

```bash
python .github/skills/dibs/scripts/dibs.py tag TASK-001 --workspace . --add database
```

`list`, `next`, `claim-next`, and `export` can filter by `--type`, repeatable `--tag`, and inclusive ISO-8601 timestamp ranges:

```bash
python .github/skills/dibs/scripts/dibs.py list --workspace . --type feature --tag database --updated-after 2026-09-01T00:00:00Z
```

Existing databases retain their timestamps and upgrade in place when `init` migrates them to schema version 3.

## For humans: checking status

You don't need to read raw JSON or learn the CLI to see what's going on. Four read-only commands are available (see Installation above for how each assistant exposes them):

| Claude Code | Copilot Chat | Shows |
|---|---|---|
| `/dibs:list [status]` | `/dibs-list` | All tasks and their status; the CLI also filters by timestamps, type, and tags |
| `/dibs:show <TASK-ID>` | `/dibs-show` | One task's spec, owner/lease, dependencies, and handoff history |
| `/dibs:next` | `/dibs-next` | Tasks that are ready to claim right now |
| `/dibs:events [TASK-ID]` | `/dibs-events` | The audit log, optionally scoped to one task |

These only read the database — they can't claim, block, or complete work, so they're safe to run at any time without affecting running agents.

## Requirements

Python 3, standard library only — no third-party packages, no server process. Works from any local disk path; network paths are rejected.

Maintained by [Brendan Lackey](https://github.com/MisterChief95). Project source and documentation are hosted at [MisterChief95/dibs](https://github.com/MisterChief95/dibs).

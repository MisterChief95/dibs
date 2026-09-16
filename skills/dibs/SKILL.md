---
name: dibs
description: Coordinate multiple local agents sharing one workspace with dependency-aware tasks, expiring ownership leases, path or resource reservations, handoffs, and an auditable SQLite state store. Use when parallel workers need to avoid overlapping edits and transfer work safely; do not use merely to spawn agents or manage remote jobs.
---

# Dibs

Use `scripts/dibs.py` as the shared source of truth while multiple agents work in the same local workspace. The script records coordination state; it does not launch, stop, or message agents.

Resolve the script path relative to this loaded `SKILL.md`: `../../scripts/dibs.py`. Before running a command, substitute its absolute path for `<DIBS_SCRIPT>` below. Never resolve the script from the workspace; the plugin may be installed in a host-managed cache. `--workspace` is required (the project being coordinated, never the skill's install location; workspaces and databases inside the skill directory are rejected). Pass the same workspace and database to every worker. Always pass a unique, stable `--actor` because the default Windows username does not distinguish agents. Put common options after the command and prefer `--json` so the complete task state is available:

```bash
python "<DIBS_SCRIPT>" next --workspace "." --actor "agent-1" --json
```

Instead of repeating flags, a worker may set `DIBS_WORKSPACE`, `DIBS_DB`, `DIBS_ACTOR`, and `DIBS_TOKEN`; explicit flags override them.

The default database is `WORKSPACE/.dibs/tasks.sqlite3`. Keep it on a local disk; network paths are rejected. If using a nondefault `--db`, use that same path for every command.

## Coordination contract

- Task `work_areas` do not create reservations by themselves. Supply `--reserve-file`, `--reserve-tree`, or `--resource` when claiming, or pass `--reserve-work-areas` to `claim`, `claim-next`, or `resume` to reserve the claimed task's work areas (existing directories become trees, everything else files; globs are rejected). Add reservations before touching more shared state.
- Use the narrowest literal reservation that covers the work. Reservations do not accept globs. A tree conflicts with every reservation at or below that path; a file conflicts only at that path. Named resources cover non-file exclusivity such as `git-index`.
- Keep the `lease_token` private to the owning worker and use the exact same actor on every owned mutation.
- Every durable coordination mutation advances `task.revision`; ephemeral tag changes do not. The revision is an optimistic-concurrency guard that prevents a stale worker from overwriting newer task state. `resume`, `reclaim`, and unowned `amend` require the current revision. Lease-holding commands accept `--revision` as an optional extra guard; the token already proves ownership.
- Renew with `heartbeat` before the lease expires. Choose `--lease-seconds` long enough to reach the next renewal, not as a substitute for heartbeats.
- Stop editing before any ownership-releasing command and pass `--ack-quiescent`. Never reclaim an expired lease until the old worker has been stopped or inspected and is known to be quiescent.
- An expired lease becomes abandoned and blocked, but its reservations remain protective. The original owner may recover it with `heartbeat` using the same token and actor, as long as nobody has reclaimed it. Anyone else uses `reclaim`, rather than bypassing or duplicating those reservations.
- Only `done` satisfies a dependency. `cancelled`, `review`, and `blocked` do not make dependents ready.

## Set up work

Initialize once from the workspace root:

```bash
python "<DIBS_SCRIPT>" init --workspace "." --actor "coordinator" --json
```

The default journal is WAL. If initialization reports that the bundled SQLite lacks the required WAL fix, retry explicitly with `--journal delete`. Do not choose delete mode preemptively on a supported runtime.

Import a UTF-8 JSON plan with this shape:

```json
{
  "source": {"summary": "Optional plan provenance"},
  "tasks": [
    {
      "id": "TASK-001",
      "title": "Implement the shared helper",
      "priority": "P1",
      "depends_on": [],
      "work_areas": ["src/helper.py"],
      "description": "Define the behavior and intended scope.",
      "acceptance": ["Targeted test passes"],
      "type": "feature",
      "tags": ["backend", "coordination"]
    }
  ]
}
```

The original seven fields remain required. `type` and `tags` are optional; type defaults to `task`, and tags default to an empty list. Types and tags use lowercase letters, numbers, dots, underscores, and hyphens. IDs match `[A-Z][A-Z0-9]*-<digits>`, priorities are `P0` through `P3`, dependencies must exist, dependency cycles are rejected, list values must be unique nonempty strings, and `acceptance` must not be empty. Import is additive and idempotent for identical existing specs; changing an existing task requires `amend` with optimistic-concurrency arguments.

Tasks record `created` and `updated` timestamps in the database. `list`, `next`, `claim-next`, and `export` accept `--type`, repeatable `--tag` (all supplied tags must match), and inclusive `--created-after`, `--created-before`, `--updated-after`, and `--updated-before` ISO-8601 filters. `list`, `next`, and `export` also accept `--status`. For human output, `list --fields id,status,created,updated,work-time,title` selects columns; `work-time` is total active lease time, capped at lease expiry.

## Worker lifecycle

1. Inspect `next --json` and `show TASK --json` before choosing work.
2. Claim a specific task, or atomically choose one with `claim-next`, while requesting all known reservations:

```bash
python "<DIBS_SCRIPT>" claim TASK-001 --workspace "." --actor "agent-1" --reserve-file "src/helper.py" --resource "git-index" --lease-seconds 600 --json
```

3. Retain the returned token (pass `--token` or set `DIBS_TOKEN`).
4. Use `heartbeat` to renew, `reserve` or `release` to adjust scope, `note` for a short audit-log update, and `handoff` to record structured context without releasing ownership. `note` needs no lease, so coordinators and reviewers can comment on any task.
5. Run the task's acceptance checks. Then submit `review`, `complete`, `block`, or `cancel` with a handoff JSON file and `--ack-quiescent`. These commands release the lease and all remaining reservations.

Handoff files use this schema. Only `summary` is required; omitted arrays default to empty, and unknown keys are rejected:

```json
{
  "summary": "What was accomplished or discovered.",
  "next_steps": ["Concrete follow-up"],
  "changed_files": ["src/helper.py"],
  "checks": ["python -m unittest tests.test_helper"],
  "blockers": []
}
```

Pass `--file -` to read handoff, import, or amend JSON from stdin instead of a file. `review` and `complete` require nonempty check evidence. `complete` rejects unresolved blockers and unfinished dependencies. `block` requires at least one specific blocker.

To defer or drop work nobody has claimed, run `block` (from `todo` or `review`) or `cancel` (from `todo`, `blocked`, or `review`) without a token, passing the current `--revision`, `--ack-unowned`, and a handoff explaining why. A task with any lease, including an abandoned one, still requires its owner's token. Deferred work returns to progress through `resume`.

Resume `blocked` or `review` work with its current revision; this creates a fresh token. For an abandoned lease, first inspect the task, confirm the previous worker is quiescent, then use `reclaim --revision N --ack-quiescent`; reclaim preserves the abandoned task's reservations and returns a new token.

Use `tag TASK --add TAG` or `--remove TAG` to change ephemeral board metadata without claiming the task or changing its revision. The change refreshes `updated` and records an audit event. Both flags are repeatable.

## Command routing

- Inspect: `list`, `show`, `next`, `events`.
- Acquire: `claim`, `claim-next`, `resume`, `reclaim`.
- Maintain an owned task: `heartbeat`, `reserve`, `release`, `note`, `handoff`.
- Release ownership: `block`, `review`, `complete`, `cancel`.
- Administer: `init`, `import`, `amend`, `tag`, `export`, `backup`.

Use `COMMAND --help` for exact flags. `export --file` and `backup --file` create new files exclusively and will not overwrite an existing destination. `amend` requires the current revision plus the lease token for owned work, or `--ack-unowned` for unowned work.

## Conflict handling

Exit codes are `0` success, `1` internal error (a tool bug; report it, do not retry), `2` conflict, `3` invalid input, `4` storage failure, and `5` not found (including a missing `--file`). With `--json`, inspect `error.code` and `error.message`.

On a conflict, do not blindly retry a stale mutation. Run `show TASK --json`, re-evaluate ownership, status, dependencies, revision, and reservations, then choose the valid transition. A storage lock may be retried after writers finish; other storage errors require fixing the reported database or runtime condition.

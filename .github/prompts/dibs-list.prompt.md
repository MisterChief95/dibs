---
agent: agent
description: List dibs tasks and their current status
---
Run `python scripts/dibs.py list --workspace . --json` in the terminal. If the user requests columns, pass `--fields FIELD,...`, choosing from `id`, `priority`, `status`, `ready`, `owner`, `created`, `updated`, `title`, `type`, `tags`, and `work-time`; `work-time` is total active lease time. Present the requested columns as a compact table, or default to ID, title, status, priority, and owner if claimed. Do not print raw JSON.

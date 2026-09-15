---
description: List dibs tasks and their current status
argument-hint: [status]
---

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/dibs/scripts/dibs.py" list --workspace "." --json
```

If an argument was given (`$ARGUMENTS`), it is a status filter — one of `todo`, `in_progress`, `blocked`, `review`, `done`, `cancelled` — so add `--status $ARGUMENTS` to the command above instead of running it unfiltered.

Present the result as a compact table: ID, title, status, priority, owner (if claimed). Do not dump raw JSON at the user.

---
description: Show one dibs task's spec, lease, and handoff history
argument-hint: <TASK-ID>
---

Task ID: `$ARGUMENTS`

If no task ID was given, ask the user for one instead of guessing.

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py" show $ARGUMENTS --workspace "." --json
```

Present the task in readable form for the user: title, priority, status, revision, dependencies (and whether each is done), work areas, current owner/lease expiry if claimed, active reservations, and the handoff/note history in chronological order. Do not dump raw JSON.

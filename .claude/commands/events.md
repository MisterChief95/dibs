---
description: Show the dibs audit log, optionally for one task
argument-hint: [TASK-ID]
---

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py" events --workspace "." --json --limit 50
```

If an argument was given (`$ARGUMENTS`), it is a task ID — add `--task $ARGUMENTS` to the command above to scope the log to that task.

Present the events as a chronological timeline (oldest first or newest first, whichever reads better): timestamp, actor, event type, and a short description of what happened. Do not dump raw JSON.

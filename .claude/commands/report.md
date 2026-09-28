---
description: Print paste-ready Markdown for dibs tasks (tickets, stories, status updates)
argument-hint: [TASK-ID ...] [--status STATE] [--tag TAG] [--updated-after TIME]
---

Arguments: `$ARGUMENTS`

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py" report $ARGUMENTS --workspace "."
```

With no task IDs it reports every task matching the filters (`--status`, `--type`, `--tag`, `--created-after/before`, `--updated-after/before`). Show the Markdown output verbatim in a single fenced `markdown` block so the user can copy it into a ticket, story, PR, or status update. Do not rewrite or summarize it.

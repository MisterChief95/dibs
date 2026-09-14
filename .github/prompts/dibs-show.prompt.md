---
agent: agent
description: Show one dibs task's spec, lease, and handoff history
---
Task ID: ${input:taskId:Which task ID?}

Run `python .github/skills/dibs/scripts/dibs.py show ${input:taskId} --workspace . --json` in the terminal, then summarize the task's title, priority, status, revision, dependencies, work areas, current owner/lease if claimed, and its handoff/note history in chronological order. Do not print raw JSON.

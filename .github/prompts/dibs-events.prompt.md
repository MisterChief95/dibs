---
mode: agent
description: Show the dibs audit log, optionally for one task
---
Task ID (optional): ${input:taskId:Leave blank for the full log}

Run `python .github/skills/dibs/scripts/dibs.py events --workspace . --json --limit 50` in the terminal, adding `--task ${input:taskId}` if a task ID was given, then present it as a chronological timeline: timestamp, actor, event type, short description. Do not print raw JSON.

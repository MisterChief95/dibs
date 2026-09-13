# Multi-agent coordination with dibs

If more than one coding agent (or an agent alongside a human) may be editing this workspace at the same time, use `dibs` before making changes, to avoid two agents touching the same files.

- Source of truth: [`.github/skills/dibs/scripts/dibs.py`](.github/skills/dibs/scripts/dibs.py) — stdlib-only Python, nothing to install.
- Full coordination contract (claim, heartbeat, reserve, handoff, complete/block/cancel): [`.github/skills/dibs/SKILL.md`](.github/skills/dibs/SKILL.md). Read it before claiming or completing a task.
- Quick check of what's ready to claim: `python .github/skills/dibs/scripts/dibs.py next --workspace . --json`

If you're the only agent working in this repo right now, you can ignore all of this.

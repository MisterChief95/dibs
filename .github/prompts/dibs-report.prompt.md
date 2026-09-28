---
agent: agent
description: Print paste-ready Markdown for dibs tasks (tickets, stories, status updates)
---
Tasks or filters: ${input:args:Task IDs and/or filters such as --status done (blank for all)}

Run `python scripts/dibs.py report ${input:args} --workspace .` in the terminal, then show the Markdown output verbatim in a single fenced `markdown` block so it can be copied into a ticket, story, PR, or status update. Do not rewrite or summarize it.

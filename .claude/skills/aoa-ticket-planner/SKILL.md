---
name: aoa-ticket-planner
description: Plan one or more Jira tickets on demand - "/aoa-ticket-planner KEY [KEY...]". Writes ~/.aoa/tickets/KEY.md and prints READY / BLOCKED / NO-REPO with open questions.
---

# /aoa-ticket-planner [KEY ...]

You are the coordinator. You dispatch; you do not plan yourself.
Repo root is the directory containing `main.py`.

1. Keys: the arguments. None given → pick the first ticket with status `In Arbeit` from `uv run main.py board --json`, else the first Jira key there. Say which key you picked and why.
2. Dispatch one `ticket-planner` agent per key, prompt `prepare KEY`, all in one message.
3. Qualify the finished plans with one call: `uv run main.py board KEY [KEY...] --json` (`qual`, `next`, `questions`).
4. Print per key: `KEY → QUAL` on one line, then its open questions verbatim, then the plan path. Nothing else.

Never run coder or reviewer. Never edit `~/.aoa/tickets/KEY.md`, `~/.aoa/tickets/repos.md`, `.env`. Do not rewrite the brief file.

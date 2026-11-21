---
name: aoa-wiki
description: Knowledge-base wiki in ~/.aoa/wiki/ - "/aoa-wiki <question>" answers from it, "ingest [KEY|today]" updates it from tickets, briefs, errors and memory, "decision: ..." / "request: ..." file a page, "lint" checks it.
---

# /aoa-wiki

The wiki in `~/.aoa/wiki/` is maintained by you. Repo root is the directory containing `main.py`. `~/.aoa/wiki/README.md` is the schema; read it only before creating a new kind of page.

Generated, never edited by hand: `~/.aoa/wiki/tickets.md` (`uv run main.py board --wiki`). `~/.aoa/wiki/build_*.py` are optional generators of your own (index, catalog, view); if one exists, its output is generated too and you run it where a step below says so. Without a `build_index.py`, keep `index.md` by hand: one line per page.

Never in the wiki: time tracking, hours. Passwords, tokens, keys, `.env` content. Private contact details (e-mail, phone, postal address, IBAN). Anything about end users or customers' contacts beyond a numeric ID. Raw prompt or transcript text. Allowed: colleagues and customer staff by name and work role, hostnames and internal IPs, commands.

## `<question>` (default)
1. Grep `~/.aoa/wiki/` for the names, hosts or keys in the question (`index.md` has one line per page). Read only the pages that match. Ticket state: `uv run main.py board KEY`.
2. Answer short, with the page paths you used.
3. Wiki cannot answer → look in `~/.aoa/tickets/`, `~/.aoa/briefs/`, `~/.aoa/errors/`, memory, the clone. Answer, then write the answer into the fitting page (or a new one), update the index (step 4 of ingest) and add a `log.md` line.

## `ingest [KEY|today]`
1. Sources: `KEY` → that ticket's files. No argument or `today` → `find ~/.aoa/tickets ~/.aoa/briefs ~/.aoa/errors -newer ~/.aoa/wiki/log.md -name '*.md'`.
2. For each source: update the repo/project/howto pages it touches and file decisions it states explicitly. Ticket state is not written anywhere by hand.
3. Append one `log.md` line per source: `## [YYYY-MM-DD] ingest | <key> | <what changed>`.
4. Run `uv run main.py board --wiki`, then every `~/.aoa/wiki/build_*.py` that exists (`uv run <script>`). No `build_index.py` → add or fix the `index.md` lines of the pages you touched.
5. Print the pages touched. Nothing else.

## `decision: <text>` / `request: <text>`
Write `~/.aoa/wiki/decisions/YYYY-MM-DD-<slug>.md` or `~/.aoa/wiki/requests/YYYY-MM-DD-<slug>.md` in the format of the existing pages, add a log line, update the index (step 4 of ingest). A request without a Jira key gets the next free `TODO-N` (never reuse a number) as `~/.aoa/tickets/TODO-N.notes.md`.

## `lint`
Run step 4 of ingest first. Then check: contradictions between pages; broken `[[links]]`; pages a source has outdated (the `find` from ingest step 1); forbidden content (grep for e-mail addresses, phone numbers, IBANs, hours). Print the findings as a list. Change nothing until the user says so.

## Rules
- Sources are never edited from here. Never read `.env`.
- Every page names its sources at the bottom. No fact without a source; write "unknown" instead of guessing.
- Never run planner, coder or reviewer from here.

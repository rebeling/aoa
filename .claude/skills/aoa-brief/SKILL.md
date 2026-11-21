---
name: aoa-brief
description: Morning brief - sync Jira tickets, list and qualify all, write ~/.aoa/briefs/YYYY-MM-DD.md. Never plans. Also "tempo" or "KEY: note".
---

# /aoa-brief

You are the coordinator. You run agents; you do not plan, code or review yourself.
Repo root is the directory containing `main.py`. Run all commands from there.

## Mode A — no argument (morning)

1. `uv run main.py sync --json`. Exit 2 → print the stderr line and stop. It returns `new`, `gone`, `changed` (per-field old/new), `touched` (Jira updated the ticket without a field change: `new_comments` and who wrote the last one) and `unchanged`, and refreshes `~/.aoa/tickets/snapshot.json`. Timetracking bucket keys are already left out.
2. `uv run main.py board --json` (add the `gone` keys as arguments in a second call if there are any). No Jira call; it reads `~/.aoa/tickets/` and the ticket worktrees and returns per ticket: `title`, `status`, `priority`, `url`, `qual`, `next`, `plan`, `questions`, `notes_newer`, `report` (date, state, next), `review`, `repos`, `git` (branch, dirty, unpushed), `resume`, `files_under`. Empty fields are omitted. **This is the qualification. Do not open plan, notes or review files to redo it.**
   Open a ticket's files only when:
   - `qual` is BLOCKED → read `~/.aoa/tickets/KEY.notes.md` and the previous brief row: questions the notes answer no longer count, and a blocker a human stated replaces "Clarify N questions" as next step. Never reset a human blocker.
   - the key is in `new`, `changed` or `touched` and has a plan → read the plan's open questions for the section below.
   - `next` is empty (TODO rows without a report) → copy the next step from the previous brief's row.
3. Never dispatch `ticket-planner` here. Planning is the user's call via `/aoa-ticket-planner KEY`. In the changes line name every ticket that needs it: `qual` UNPLANNED → "KEY has no plan, run /aoa-ticket-planner KEY"; in `changed`/`touched` or `notes_newer` true → "KEY changed/has new notes, run /aoa-ticket-planner KEY".
   What the quals mean (computed by the script, in this order): DONE closed in the notes · REPORTED newest `REPORT` is newer than the plan, next step = its `Next:` · UNPLANNED no `~/.aoa/tickets/KEY.md` · FEEDBACK review verdict OK · NO-REPO no repo in plan or notes · BLOCKED open questions in the plan · READY otherwise. Next step is a short human action, at most six words; commands live in the legend, not in the table.

4. Tempo only when asked (`/aoa-brief tempo`, or the user says "with tempo" / "time"): dispatch the `reporter` agent (your own, not part of this repo; no such agent → say so and skip): "tempo entries since the last working day for these repos: <every distinct non-empty path in ~/.aoa/tickets/repos.md>". These are separate clones; the reporter handles each as its own repo. Print the reporter's table in the terminal after the brief summary; it never goes into the brief file. If `~/.aoa/briefs/tempo-open.md` exists, its days are still unbooked: include them as stated there.
5. Write `~/.aoa/briefs/YYYY-MM-DD.md`:
   ```markdown
   # Brief YYYY-MM-DD

   Jira: <base>/jira/for-you?tab=assigned   (base = any ticket url up to the host, e.g. https://your-site.atlassian.net)

   ## Changes since last sync
   New: ... · Gone: KEY (+ last REPORT state, or "no report from ticket session") · Changed: KEY field old -> new (one line per change; description changes just say "description changed")
   or: Nothing changed.

   | Key | Title | Status | Prio | Qual | Next step |
   |---|---|---|---|---|---|
   (the table is the output of `uv run main.py board --md`, taken as printed; change only the cells step 2 told you to change. Keys link to Jira. TODO-N rows = items without a Jira ticket, numbered in order of adding, never reused; a TODO leaves the table when its notes get a `- YYYY-MM-DD: done (user).` line)

   Qual:
   - REPORTED: a ticket session reported a milestone; next step comes from that report.
   - DONE: closed in the notes but still open in Jira. Close it there.
   - FEEDBACK: implemented and reviewed OK, pushed. Waiting for feedback from stage or ticket writer.
   - READY: plan complete, repo known. `/aoa-work KEY` can start.
   - BLOCKED: open questions. Answer: `/aoa-brief KEY: <answer>`. Skip: `/aoa-work KEY ignore questions`.
   - NO-REPO: no clone named. Tell it via `/aoa-brief KEY: repo: <path>`.
   - UNPLANNED: no plan file. `/aoa-ticket-planner KEY`.

   ## Open questions (only tickets in `new` or `changed` that have a plan)
   ### KEY — summary
   1. ...

   ## Sessions
   (one code block; per row that has a `resume` value a comment line `# KEY  short title` and that `resume` line as given. Never invent an id.)

   ```
6. Print to the terminal: the changes line, the table, the legend line, the count of UNPLANNED/FEEDBACK/READY/BLOCKED/NO-REPO, the Sessions code block, and the brief path. Nothing else.

## Mode A with argument `tempo`

Same as Mode A, with step 4 included. Tempo output is terminal only.

## Mode B — argument `KEY: note`

Notes are free text. Forms the planner understands specially: `repo: <path>` (which clone, may repeat), `base: <branch>` (branch the ticket branch off it), `branch: <branch>` (work directly on that branch, no new branch). Everything else is an answer or decision that overrides the ticket text.

1. Append `- YYYY-MM-DD: <note>` to `~/.aoa/tickets/KEY.notes.md` (create if missing).
2. Dispatch `ticket-planner` with `prepare KEY`.
3. Qualify as in A3 and print one line: `KEY → <QUAL>. <next step>` plus remaining open questions if any.
Do not rewrite the brief file.

## Between briefs

`uv run main.py board` prints the same status as a terminal table at no model cost; `uv run main.py board --sync --watch 300` keeps it on screen and re-checks Jira every five minutes. A question like "what is the state" is answered by running `board`, not by a new brief.

## Rules

- Never run coder or reviewer from here. Mode A never runs the planner either; only Mode B does.
- Never edit `~/.aoa/tickets/KEY.md`, `~/.aoa/tickets/repos.md`, `.env`.
- If an agent fails, say which ticket and why; do not retry more than once.

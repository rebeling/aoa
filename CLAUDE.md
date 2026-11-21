# Private data

This repo is public. Everything private lives outside it in `~/.aoa` (or `$AOA_HOME`): `tickets/`, `wiki/`, `briefs/`, `errors/`, `reviews/`, `memory/`, `sites.md`, `diag.sh`, `.env`. Never create, copy or link any of it inside the repo folder, and never put hostnames, IPs, customer or ticket details into files of this repo; examples here stay generic. `./setup.sh` prepares a new machine. Paths below are written as `~/.aoa/...`; if `AOA_HOME` is set (shell, or `.env` in the repo, see `.env.example`), read them as that folder.

# Jira tickets

This repo has a read-only Jira CLI in `main.py`. Use it to fetch the user's tickets
instead of calling the Jira API directly. Credentials come from `~/.aoa/.env`
(never read or print it) or from `JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN` in the shell; if missing, exit code is 2 and the message
names the missing variable — tell the user, do not guess values.

```sh
uv run main.py mytickets --json          # open tickets assigned to the user
uv run main.py get KEY [KEY...] --json   # summary, description, status, labels, url; --comments adds the thread
uv run main.py sync --json               # new / gone / changed since last snapshot, refreshes it
uv run main.py comment KEY "text"        # the only write: post a comment ("-" = text from stdin)
uv run main.py board [KEY...] --json     # no Jira call: qual, next step, last report, review, git state per ticket, from ~/.aoa/tickets/
```

"What is the state of X / of everything" → run `board`, do not open plan and notes files to work it out.
`board` prints a terminal table, `board --md` the brief table, `board --sync --watch 300` a live view, `board --wiki` rewrites `~/.aoa/wiki/tickets.md`.

Prefer `--json` when consuming output. `comment` is the only Jira write and runs only when the user asks for it; never transitions, edits or deletes.

If `~/.aoa/diag.sh` exists (your own script), it reads the usual first facts from a server in one read-only ssh call; run it without arguments for its usage and use it before single ssh commands.

# Ticket planning

`.claude/agents/ticket-planner.md` turns one ticket into `~/.aoa/tickets/<KEY>.md` (what,
problem, solution, repo/files, open questions). `~/.aoa/tickets/repos.md` lists known local clones. Which
clone a ticket belongs to is decided by the user per ticket (`/aoa-brief KEY: repo <path>`) or by an
existing clone whose branch contains the key. Never guess a repo. Timetracking bucket tickets (key prefix `AOA_SKIP_PREFIX` in
`~/.aoa/.env`) are never work; the CLI leaves them out of `mytickets`, `sync` and `board`.

# Workflow

- `/aoa-brief` morning, once a day in a fresh session: `main.py sync` diffs tickets against `~/.aoa/tickets/snapshot.json`, `main.py board` qualifies them; plans nothing; writes `~/.aoa/briefs/YYYY-MM-DD.md`. `/aoa-brief tempo` adds Tempo estimates. Status during the day: `uv run main.py board`.
- `/aoa-brief KEY: note` records a change for one ticket in `~/.aoa/tickets/KEY.notes.md` and re-plans it.
- `/aoa-ticket-planner KEY [KEY...]` plans given tickets on demand. `/aoa-brief` never plans a ticket just because its notes changed.
- `/aoa-work KEY` runs coder → reviewer (max 2 rounds). Workdir is the existing clone whose branch contains KEY (building on the user's uncommitted work there), else a new git worktree `<repo>-KEY` on `fix/KEY` or `feature/KEY`. Other clones are never touched. Nothing is committed; the user commits.
- `/aoa-error <pasted log>` runs the `error-log` agent: reads server logs read-only, finds the code, writes `~/.aoa/errors/<slug>.md` (cause, suggested fix) and a row in `~/.aoa/errors/index.md`. `/aoa-error list` shows open ones, `/aoa-error fixed <slug>` closes one. Never fixes anything.
- Agents: `ticket-planner`, `coder`, `reviewer`, `error-log` in `.claude/agents/`. Skills in `.claude/skills/`.
- Per-ticket files in `~/.aoa/tickets/`: `KEY.md` plan, `KEY.notes.md` user notes, `KEY.done.md` coder report, `KEY.review.md` verdict.

# Wiki

`~/.aoa/wiki/` is the knowledge base: tickets overview, projects, repos, how-tos, decisions, requests. Before answering a
question about a system, host, deploy step or past ticket, grep `~/.aoa/wiki/` for the name, host or key and read the page that
matches; read `~/.aoa/wiki/index.md` whole only when grep finds nothing. `~/.aoa/wiki/index.md` and `~/.aoa/wiki/tickets.md` are generated
(`uv run ~/.aoa/wiki/build_index.py`, `uv run main.py board --wiki`), never edited by hand. `/aoa-wiki` queries, ingests and
lints; `~/.aoa/wiki/README.md` is the schema. Facts go to the wiki; rules for how Claude behaves stay here and in memory.
Clone paths have one source: `~/.aoa/tickets/repos.md`.

Claude's auto memory for this project is private and lives outside the repo in `~/.aoa/memory` (set by `autoMemoryDirectory` in `.claude/settings.local.json`, per machine). It never goes into this repo.

# Commits

One line, `KEY: what changed` where a Jira ticket applies. No body, no Co-Authored-By, no Claude mention. Applies to every repo, and to MR titles and descriptions.
`TODO-N` keys are local to this hub: never in a commit message, MR text, pushed branch name or Jira comment (no `Closes TODO-N`). A TODO without Jira key gets a plain subject and a descriptive branch name.

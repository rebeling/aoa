# aoa — agent of agents

One hub you talk to. It sends specialised agents out to plan, build and review Jira tickets, and reports back.
You decide at the gates: no agent starts on its own, and nothing is committed, pushed or deployed without you.

## Quick start

Needs [Claude Code](https://docs.claude.com/en/docs/claude-code), [uv](https://docs.astral.sh/uv/) and a
[Jira Cloud API token](https://id.atlassian.com/manage-profile/security/api-tokens).

```sh
git clone https://github.com/rebeling/aoa.git ~/Desktop/aoa
cd ~/Desktop/aoa
./setup.sh            # creates ~/.aoa, the memory setting and the global /aoa-report skill
$EDITOR ~/.aoa/.env   # JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
claude                # then type /aoa-brief
```

## Code here, your data in `~/.aoa`

This repo holds only code, skills and agents. Everything about your work stays outside it:

| In `~/.aoa` | Content |
|---|---|
| `.env` | Jira credentials; optional `AOA_SKIP_PREFIX` (key prefix of timetracking tickets to leave out) |
| `tickets/` | Per ticket: `KEY.md` plan, `KEY.notes.md` your notes and session reports, `KEY.done.md`, `KEY.review.md`; `repos.md` your local clones; `snapshot.json` last Jira state |
| `briefs/` | One brief per day |
| `wiki/` | What outlives a ticket: projects, repos, how-tos, decisions |
| `errors/` | Prod error reports from `/aoa-error` |
| `sites.md` | Servers per site for `/aoa-error` (ssh target, docroot, logs, clone) |
| `memory/` | Claude's memory for this project |

Another folder: set `AOA_HOME` (see `.env.example`). Pull the repo on any machine; sync `~/.aoa` between your
own machines by your own means. The skills assume the repo sits at `~/Desktop/aoa`; cloned elsewhere, change
that path in the `aoa-report` and `aoa-session` skills.

## A day

1. **`/aoa-brief`**, once in the morning. Syncs Jira, shows what changed, qualifies every ticket, writes the brief.
   It plans and starts nothing.
2. **`uv run main.py board`** any time after. Same table, instant, no Jira call, no model.
   `board --sync --watch 300` keeps a live view open.
3. **`/aoa-ticket-planner KEY`** writes the plan. Answers and decisions: `/aoa-brief KEY: <note>`.
4. **`/aoa-session KEY`** gives the worktree one-liner and first message for a separate Claude session per ticket.
   Or `/aoa-work KEY`: coder implements test-first, reviewer checks against the plan, at most two rounds.
5. **`/aoa-report KEY`** in the ticket session after each milestone. The board shows it with its next step.
6. You commit, push and deploy.

```
KEY     QUAL       STATUS       REPORT         GIT                  NEXT                 TITLE
TODO-3  REPORTED                11-20 pushed   0 dirty              Test on stage        Speed up deploy
ABC-12  REPORTED   In Progress  11-21 stage    2 dirty, 1 unpushed  Go live              Export fails for big orders
ABC-15  READY      To Do                                            Ready to implement   Add VAT field to invoice
ABC-17  BLOCKED    To Do                                            Clarify 2 questions  Newsletter per region
XYZ-4   UNPLANNED  To Do                                            Plan it              Login loop on mobile
```

| Qual | Meaning | You do |
|---|---|---|
| `UNPLANNED` | No plan yet | `/aoa-ticket-planner KEY` |
| `NO-REPO` | Plan exists, no clone named | `/aoa-brief KEY: repo: <path>` |
| `BLOCKED` | Open questions for the ticket writer | `/aoa-brief KEY: <answer>` |
| `READY` | Plan complete, repo known | `/aoa-session KEY` or `/aoa-work KEY` |
| `REPORTED` | A ticket session reported a milestone | The report's next step |
| `FEEDBACK` | Implemented, reviewed OK | Wait for stage test or ticket writer |
| `DONE` | Closed in your notes, still open in Jira | Close it in Jira |

Work without a Jira ticket: tell the hub "new todo: …". It becomes `TODO-1`, `TODO-2`, … These keys are local:
never in commits, merge requests, branch names or Jira comments. A notes line `- YYYY-MM-DD: done (user).` closes one.

Also: `/aoa-error <pasted log>` finds the cause of a prod error read-only and suggests a fix (never fixes);
`/aoa-wiki <question>` answers from the wiki, `/aoa-wiki ingest` updates it.

## Principles

- **Simple.** Markdown files and one small Python CLI on the standard library. Every agent has one job.
- **Always the repo.** A repo is never guessed: it comes from your `repo: <path>` note or from a clone whose branch contains the ticket key.
- **Knowledge stays.** What a ticket taught goes into the wiki.
- **Private stays private.** No ticket, host or customer detail in this repo; examples are generic.

## Agents

`ticket-planner`, `coder`, `reviewer`, `error-log` in `.claude/agents/`; the commands above are skills in
`.claude/skills/`. Planned: watcher (pipelines, merge requests), releaser, verifier, scribe.

## CLI

The skills call `main.py`; you rarely need it yourself. It reads Jira Cloud and can post a comment, nothing else.

```sh
uv run main.py mytickets [--json]                 # open tickets assigned to you
uv run main.py get ABC-12 [--json] [--comments]   # one or more tickets in full
uv run main.py sync [--json]                      # new / gone / changed / newly commented since last run
uv run main.py board [KEY...] [--json|--md]       # status from ~/.aoa/tickets and the ticket worktrees
uv run main.py comment ABC-12 "text"              # the only write; "-" reads the text from stdin
uv run pytest
```

`sync` fetches details only for tickets whose `updated` stamp changed: a quiet morning costs one request.
Exit codes: `0` ok, `1` Jira or HTTP error, `2` missing setting.

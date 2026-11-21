"""Read-only CLI for the user's Jira Cloud tickets.

Private data (tickets, wiki, briefs, memory, credentials) lives outside this repo in ~/.aoa,
or wherever AOA_HOME points (shell variable, or a `.env` next to this script that may hold
nothing but AOA_HOME). Jira config: JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN from the shell
or from $AOA_HOME/.env (shell wins). See .env.example.

    uv run main.py mytickets [--json]
    uv run main.py get KEY [KEY...] [--json] [--comments]
    uv run main.py sync [--json] [--snapshot PATH]
    uv run main.py board [KEY...] [--json|--md] [--sync] [--watch SECONDS] [--wiki]   # offline status from ~/.aoa/tickets
    uv run main.py comment KEY "text"      # the only write; "-" reads text from stdin
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlencode
from urllib.request import urlopen  # patched in tests

REQUIRED_ENV = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


def aoa_home(env=None):
    """Folder with the private data. Never inside the repo: the repo is public."""
    env = os.environ if env is None else env
    return Path(env.get("AOA_HOME") or "~/.aoa").expanduser()
MY_TICKETS_JQL = (
    "assignee = currentUser() AND statusCategory != Done "
    "ORDER BY priority ASC, updated DESC"
)
LIST_FIELDS = "summary,status,priority,updated"
ISSUE_FIELDS = "summary,status,priority,issuetype,description,labels"


class CliError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def load_dotenv(path, env):
    """Read KEY=VALUE lines from path into env. Non-empty env values win. `# comment` after a value is dropped."""
    if not Path(path).is_file():
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value.strip()).strip("'\"")
        if not env.get(key.strip()):
            env[key.strip()] = value


def load_config(env):
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        raise CliError(f"Missing env var(s): {', '.join(missing)}", 2)
    return {
        "base_url": env["JIRA_BASE_URL"].rstrip("/"),
        "email": env["JIRA_EMAIL"],
        "token": env["JIRA_API_TOKEN"],
    }


def request(cfg, path, params, body=None):
    url = f"{cfg['base_url']}{path}" + (f"?{urlencode(params)}" if params else "")
    creds = base64.b64encode(f"{cfg['email']}:{cfg['token']}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, headers=headers, data=data, method="POST" if data else "GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            messages = json.load(e).get("errorMessages", [])
        except (ValueError, AttributeError):
            messages = []
        detail = "; ".join(messages) or e.reason
        raise CliError(f"HTTP {e.code}: {detail}", 1) from e
    except urllib.error.URLError as e:
        raise CliError(f"Connection failed: {e.reason}", 1) from e


def name(field):
    return field["name"] if field else ""


def browse_url(cfg, key):
    return f"{cfg['base_url']}/browse/{key}"


def my_tickets(cfg):
    rows = []
    params = {"jql": MY_TICKETS_JQL, "fields": LIST_FIELDS, "maxResults": 100}
    while True:
        page = request(cfg, "/rest/api/2/search/jql", params)
        for it in page.get("issues", []):
            if it["key"].startswith(SKIP_PREFIX):
                continue
            f = it["fields"]
            rows.append({
                "key": it["key"],
                "status": name(f.get("status")),
                "priority": name(f.get("priority")),
                "summary": f.get("summary") or "",
                "updated": f.get("updated") or "",
                "url": browse_url(cfg, it["key"]),
            })
        token = page.get("nextPageToken")
        if not token:
            return rows
        params["nextPageToken"] = token


def get_comments(cfg, key):
    page = request(cfg, f"/rest/api/2/issue/{key}/comment", {"orderBy": "created"})
    return [{
        "id": c.get("id"),
        "author": (c.get("author") or {}).get("displayName") or "",
        "created": c.get("created") or "",
        "body": c.get("body") or "",
    } for c in page.get("comments", [])]


def get_issue(cfg, key, comments=False, count_comments=False):
    fields = ISSUE_FIELDS + (",comment" if count_comments else "")
    it = request(cfg, f"/rest/api/2/issue/{key}", {"fields": fields})
    f = it["fields"]
    issue = {
        "key": it["key"],
        "type": name(f.get("issuetype")),
        "status": name(f.get("status")),
        "priority": name(f.get("priority")),
        "labels": f.get("labels") or [],
        "summary": f.get("summary") or "",
        "description": f.get("description") or "",
        "url": browse_url(cfg, it["key"]),
    }
    if comments:
        issue["comments"] = get_comments(cfg, key)
    if count_comments:
        thread = (f.get("comment") or {}).get("comments") or []
        issue["comment_count"] = (f.get("comment") or {}).get("total", len(thread))
        if thread:
            issue["last_comment"] = {
                "author": (thread[-1].get("author") or {}).get("displayName") or "",
                "created": thread[-1].get("created") or "",
            }
    return issue


DIFF_FIELDS = ("summary", "status", "priority", "type", "labels", "description")
DEFAULT_SKIP_PREFIX = "TIME-"  # timetracking buckets, never work; override with AOA_SKIP_PREFIX
SKIP_PREFIX = DEFAULT_SKIP_PREFIX


def load_snapshot(path):
    """Stored tickets by key, without timetracking buckets. A missing or broken file counts as empty."""
    try:
        snap = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in snap.items() if not k.startswith(SKIP_PREFIX)} if isinstance(snap, dict) else {}


def sync(cfg, snapshot_path):
    """Compare current tickets with the stored snapshot; refresh it; return the diff."""
    path = Path(snapshot_path)
    old = load_snapshot(path)
    current = {r["key"]: r for r in my_tickets(cfg)}
    diff = {"new": [], "gone": sorted(k for k in old if k not in current),
            "changed": [], "touched": [], "unchanged": []}
    new = {}
    for key, row in current.items():
        prev = old.get(key)
        if prev and prev.get("updated") == row["updated"]:
            new[key] = prev
            diff["unchanged"].append(key)
            continue
        detail = get_issue(cfg, key, count_comments=True)
        detail["updated"] = row["updated"]
        new[key] = detail
        if prev is None:
            diff["new"].append(key)
        else:
            fields = {f: [prev[f], detail.get(f)] for f in DIFF_FIELDS
                      if f in prev and prev[f] != detail.get(f)}
            if fields:
                diff["changed"].append({"key": key, "fields": fields})
            else:
                # Jira bumped `updated` without a field change: usually a comment.
                added = (detail["comment_count"] - prev["comment_count"]
                         if "comment_count" in prev else None)
                diff["touched"].append({"key": key, "new_comments": added,
                                        "last_comment": detail.get("last_comment")})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # replace in one step: an interrupted run keeps the old snapshot
    tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
    return diff


def format_sync(diff):
    lines = []
    if diff["new"]:
        lines.append("New: " + ", ".join(diff["new"]))
    if diff["gone"]:
        lines.append("Gone: " + ", ".join(diff["gone"]))
    for ch in diff["changed"]:
        for field, (a, b) in ch["fields"].items():
            if field == "description":
                lines.append(f"{ch['key']} description changed")
            else:
                lines.append(f"{ch['key']} {field}: {a} -> {b}")
    for t in diff.get("touched", []):
        by = (t.get("last_comment") or {}).get("author")
        what = (f"{t['new_comments']} new comment(s)" if (t["new_comments"] or 0) > 0
                else "updated, no field change")
        lines.append(f"{t['key']} {what}" + (f", last comment by {by}" if by else ""))
    return "\n".join(lines) if lines else "Nothing changed."


# --- board: ticket status computed from tickets/ files, no Jira call -------------------

NOTE_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}): ?(.*)")
# A ticket is closed by a note the user stands behind: "done (user).", "(user) Done.", "closed by user: …",
# a "REPORT done" from the ticket session, or a line ending in "Status: done.". "Done: part one" does not count.
CLOSED_RE = re.compile(r"^\(user\) (done|closed)\b|^(done|closed) (\(user|by user)|^REPORT done\b|\bstatus: done\.?$", re.I)
PLAN_NEUTRAL_RE = re.compile(r"REPORT |session started ")  # notes that never call for a new plan
QUAL_NEXT = {"UNPLANNED": "Plan it", "FEEDBACK": "Waiting for feedback",
             "READY": "Ready to implement", "NO-REPO": "Name the repo", "DONE": "Close it"}
KEY_FILE_RE = re.compile(r"^([A-Z][A-Z0-9]*-(?:\d{4}-)?\d+)(\.notes|\.done|\.review)?\.md$")


def parse_notes(text):
    """Return (title, [[date, text], ...]). Lines after a dated line continue that entry."""
    title, entries = "", []
    for line in text.splitlines():
        m = NOTE_RE.match(line)
        if m:
            entries.append([m[1], m[2]])
        elif line.startswith("# ") and not title and not entries:
            title = line[2:].strip()
        elif entries and line.strip():
            entries[-1][1] += "\n" + line.strip()
    return title, entries


def section(text, heading):
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m[1].strip() if m else ""


def git_state(workdir):
    """Branch, changed files and unpushed commits of a clone, from one git call."""
    r = subprocess.run(["git", "-C", workdir, "status", "--porcelain=v2", "--branch"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    state = {"branch": None, "dirty": 0, "unpushed": None}  # unpushed None = no upstream
    for line in r.stdout.splitlines():
        if line.startswith("# branch.head "):
            state["branch"] = line[len("# branch.head "):]
        elif line.startswith("# branch.ab "):
            state["unpushed"] = int(line.split()[2])  # "+N"
        elif not line.startswith("#"):
            state["dirty"] += 1
    return state


def resume_command(workdir, home):
    """`cd <workdir> && claude --resume <id>` for the newest transcript of that workdir."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(workdir).resolve()))
    logs = list((Path(home) / ".claude" / "projects" / slug).glob("*.jsonl"))
    if not logs:
        return None
    return f"cd {shlex.quote(workdir)} && claude --resume {max(logs, key=lambda p: p.stat().st_mtime).stem}"


def ticket_row(key, tdir, snap):
    def read(suffix):
        p = Path(tdir) / f"{key}{suffix}.md"
        return p.read_text(errors="replace") if p.is_file() else None
    plan, notes, review = read(""), read(".notes"), read(".review")
    note_title, entries = parse_notes(notes or "")
    jira = snap.get(key) or {}
    row = {"key": key, "title": jira.get("summary") or "", "status": jira.get("status") or "",
           "priority": jira.get("priority") or "", "url": jira.get("url") or "",
           "in_jira": key in snap, "plan": plan is not None, "planned": None, "questions": 0,
           "notes_newer": False, "report": None, "review": None, "repos": [], "workdir": None,
           "git": None, "resume": None, "closed": False, "jira_key": None}
    if plan:
        head = plan.splitlines()[0] if plan.strip() else ""
        m = re.match(r"# \[[^\]]+\]\(([^)]+)\): ?(.*)", head)
        if m:
            row["url"], row["title"] = row["url"] or m[1], row["title"] or m[2]
        m = re.search(r"planned (\d{4}-\d{2}-\d{2})", plan)
        row["planned"] = m[1] if m else None
        row["questions"] = len(re.findall(r"^\d+\. ", section(plan, "Open questions for the ticket writer"), re.M))
        row["repos"] = re.findall(r"^- Repo: (\S+)", section(plan, "Repo and files"), re.M)
    if not row["title"]:
        row["title"] = re.sub(rf"^{re.escape(key)}\s*[—:-]*\s*", "", note_title)
    if not row["title"] and entries:
        row["title"] = re.sub(r"^(added\.|\(user\)|REPORT \w+\.)\s*", "", entries[0][1].splitlines()[0])
    session = False
    for date, text in entries:
        first = text.splitlines()[0] if text else ""
        if CLOSED_RE.search(first):
            row["closed"] = True
        elif re.match(r"reopened\b", first, re.I):
            row["closed"] = False
        m = re.match(r"Jira key ([A-Z][A-Z0-9]*-\d+)", first)
        if m:
            row["jira_key"] = m[1]
        m = re.match(r"repo:? (\S+)", text)
        if m and m[1] not in row["repos"]:
            row["repos"].append(m[1])
        m = re.search(r"session started in (\S+)", text)
        if m:
            row["workdir"], session = m[1].rstrip(".,;)"), True
        elif not session and (m := re.search(r"worktree (~?/\S+)", text)):
            row["workdir"] = m[1].rstrip(".,;)")
        m = re.match(r"REPORT (\w+)", text)
        if m:
            nxt = re.search(r"Next: (.+)", text)
            row["report"] = {"date": date, "state": m[1].lower(),
                             "next": nxt[1].strip().rstrip(".") if nxt else ""}
    row["repos"] = [r for r in row["repos"] if not r.startswith("unknown")]
    if review:
        verdicts = re.findall(r"^## Verdict: (\w+)", review, re.M)
        row["review"] = verdicts[-1] if verdicts else None
    if entries:
        row["last_note"] = entries[-1][0]
        # Only notes that carry a decision or an answer make a plan stale, not reports of progress.
        relevant = [d for d, t in entries if not PLAN_NEUTRAL_RE.match(t)]
        if plan and relevant:
            plan_file, notes_file = Path(tdir) / f"{key}.md", Path(tdir) / f"{key}.notes.md"
            same_day_later = (relevant[-1] == row["planned"] == entries[-1][0]
                              and not PLAN_NEUTRAL_RE.match(entries[-1][1])
                              and notes_file.stat().st_mtime > plan_file.stat().st_mtime)
            row["notes_newer"] = not row["planned"] or relevant[-1] > row["planned"] or same_day_later
    rep = row["report"]
    report_is_news = rep and (not row["planned"] or rep["date"] >= row["planned"])
    if row["closed"]:
        qual = "DONE"
    elif report_is_news:
        qual = "REPORTED"
    elif not plan:
        qual = "UNPLANNED" if row["in_jira"] else "-"
    elif row["review"] == "OK":
        qual = "FEEDBACK"
    elif not row["repos"]:
        qual = "NO-REPO"
    elif row["questions"]:
        qual = "BLOCKED"
    else:
        qual = "READY"
    row["qual"] = qual
    row["next"] = (rep["next"] if qual == "REPORTED" else
                   f"Clarify {row['questions']} questions" if qual == "BLOCKED" else
                   QUAL_NEXT.get(qual, ""))
    return row


def board(tdir, snapshot_path, keys=(), everything=False, home=None):
    """One row per ticket. Default: open Jira tickets plus open local keys (TODO-N)."""
    home = home or Path.home()
    snap = load_snapshot(snapshot_path)
    local = sorted({m[1] for f in Path(tdir).glob("*.md") if (m := KEY_FILE_RE.match(f.name))},
                   key=lambda k: (k.rsplit("-", 1)[0], int(k.rsplit("-", 1)[1])))
    known = [k for k in local if k not in snap] + list(snap)
    rows = [ticket_row(k, tdir, snap) for k in known + [k for k in keys if k not in known]]
    # A TODO that later got a Jira key keeps its files: show its state on the Jira row.
    by_key = {r["key"]: r for r in rows}
    for todo in rows:
        target = by_key.get(todo["jira_key"])
        if target and (todo["plan"] or todo["report"]) and not target["plan"] and not target["report"]:
            for f in ("plan", "planned", "questions", "report", "review", "repos", "workdir",
                      "qual", "next", "closed"):
                target[f] = todo[f]
            target["files_under"] = todo["key"]
    if keys:
        rows = [by_key[k] for k in keys]
    elif not everything:
        rows = [r for r in rows if r["in_jira"] or (r["key"].startswith("TODO-") and not r["closed"]
                                                    and r["jira_key"] not in snap)]
    # git state per worktree: one call each, all worktrees at once (big clones are slow to stat)
    dirs = sorted({wd for r in rows if r["workdir"] and Path(wd := os.path.expanduser(r["workdir"])).is_dir()})
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = dict(zip(dirs, pool.map(git_state, dirs)))
    for r in rows:
        wd = os.path.expanduser(r["workdir"] or "")
        if wd in states:
            r["git"], r["resume"] = states[wd], resume_command(wd, home)
    return rows


def short(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def format_board(rows):
    if not rows:
        return "No tickets."
    def git(r):
        g = r["git"]
        if not g:
            return ""
        return f"{g['dirty']} dirty" + (f", {g['unpushed']} unpushed" if g["unpushed"] else "")
    def report(r):
        return f"{r['report']['date'][5:]} {r['report']['state']}" if r["report"] else ""
    table = [("KEY", "QUAL", "STATUS", "REPORT", "GIT", "NEXT", "TITLE")] + [
        (r["key"], r["qual"], short(r["status"], 14), report(r), git(r),
         short(r["next"], 44), short(r["title"], 50)) for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(6)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)) + "  " + row[6]
                     for row in table)


def format_board_md(rows):
    lines = ["| Key | Title | Status | Prio | Qual | Next step |", "|---|---|---|---|---|---|"]
    for r in rows:
        key = f"[{r['key']}]({r['url']})" if r["url"] else r["key"]
        cells = [key, short(r["title"], 50), r["status"] or "—", r["priority"] or "—",
                 r["qual"].replace("-", "—") if r["qual"] == "-" else r["qual"], r["next"]]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines)


def wiki_state(r):
    if r["closed"]:
        return "done"
    if r["report"]:
        return r["report"]["state"]
    return "open" if r["in_jira"] or r["key"].startswith("TODO-") else "gone from Jira, no report"


def format_wiki_tickets(rows, today, history=False):
    lines = [
        "---", "type: overview", f"updated: {today}", "generated: uv run main.py board --wiki", "---",
        "# Tickets", "",
        f"Generated {today} by `uv run main.py board --wiki` from the tickets folder and the Jira snapshot. "
        "Never edit by hand." + (" Older hand-written outcomes: [[tickets-history]]." if history else ""), "",
        "State: `open` = in the Jira snapshot or an open TODO, no report yet; a report state "
        "(`implemented`, `pushed`, `stage`, `live`, `blocked`) = newest REPORT in the notes; "
        "`done` = closed in the notes.", "",
        "| Key | Title | Project | State | Repo(s) | Last report | Next | Files |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        key = f"[{r['key']}]({r['url']})" if r["url"] else r["key"]
        files = [n for n, has in (("plan", r["plan"]), ("notes", "last_note" in r),
                                  ("review " + str(r["review"]), r["review"])) if has]
        cells = [key, short(r["title"], 70), r["key"].split("-")[0], wiki_state(r),
                 ", ".join(f"`{p.replace(str(Path.home()), '~')}`" for p in r["repos"]) or "—",
                 r["report"]["date"] if r["report"] else r.get("last_note", "—"),
                 short(r["next"], 80) if not r["closed"] else "—", ", ".join(files)]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def add_comment(cfg, key, text):
    if text == "-":
        text = sys.stdin.read()
    if not text.strip():
        raise CliError("Empty comment, nothing posted.", 2)
    resp = request(cfg, f"/rest/api/2/issue/{key}/comment", None, {"body": text})
    return f"Comment {resp.get('id', '?')} posted to {key}: {browse_url(cfg, key)}"


def format_table(rows):
    if not rows:
        return "No open tickets."
    cols = ("key", "status", "priority", "summary")
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in cols[:-1]}
    def line(r):
        return "  ".join(r[c].ljust(widths[c]) for c in cols[:-1]) + "  " + r["summary"]
    header = {c: c.upper() for c in cols}
    return "\n".join([line(header)] + [line(r) for r in rows])


def format_issue(i):
    return "\n".join([
        f"# {i['key']}: {i['summary']}",
        f"Type: {i['type']}",
        f"Status: {i['status']}",
        f"Priority: {i['priority']}",
        f"Labels: {', '.join(i['labels'])}",
        f"URL: {i['url']}",
        "",
        i["description"],
    ] + [
        f"\n## {c['author']} ({c['created'][:16].replace('T', ' ')}, #{c['id']})\n{c['body']}"
        for c in i.get("comments", [])
    ])


def run_board(args, env):
    snapshot = Path(args.tickets) / "snapshot.json"
    while True:
        try:
            changes = format_sync(sync(load_config(env), snapshot)) if args.sync else None
        except CliError as e:
            if not args.watch:
                raise
            changes = f"Jira check failed, showing the last known state: {e}"
        if args.wiki:
            rows = board(args.tickets, snapshot, everything=True)
            out = Path(args.tickets).parent / "wiki" / "tickets.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            history = out.with_name("tickets-history.md").is_file()
            out.write_text(format_wiki_tickets(rows, time.strftime("%Y-%m-%d"), history))
            print(f"{out}: {len(rows)} tickets")
            return 0
        rows = board(args.tickets, snapshot, args.keys, args.all)
        slim = [{k: v for k, v in r.items() if v not in (None, False, "", [], 0)} for r in rows]
        text = (json.dumps(slim, ensure_ascii=False) if args.json
                else format_board_md(rows) if args.md else format_board(rows))
        if args.watch:
            print("\033[2J\033[H" + time.strftime("%Y-%m-%d %H:%M:%S"), end="\n\n")
        if changes:
            print(changes, end="\n\n")
        print(text, flush=True)
        if not args.watch:
            return 0
        time.sleep(max(args.watch, 60 if args.sync else 2))


def main(argv=None, env=None):
    if env is None:
        env = dict(os.environ)
        local = {}  # .env in the repo folder: path setting only, never credentials
        load_dotenv(Path(__file__).with_name(".env"), local)
        if local.get("AOA_HOME"):
            env.setdefault("AOA_HOME", local["AOA_HOME"])
        load_dotenv(aoa_home(env) / ".env", env)
    tickets_dir = aoa_home(env) / "tickets"
    global SKIP_PREFIX
    SKIP_PREFIX = env.get("AOA_SKIP_PREFIX") or DEFAULT_SKIP_PREFIX
    parser = argparse.ArgumentParser(description="Fetch Jira tickets.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("mytickets", help="open tickets assigned to me")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("get", help="full details for ticket keys")
    p.add_argument("keys", nargs="+")
    p.add_argument("--json", action="store_true")
    p.add_argument("--comments", action="store_true", help="include the comment thread")
    p = sub.add_parser("comment", help="post a comment to a ticket (the only write)")
    p.add_argument("key")
    p.add_argument("text", help='comment text, or "-" to read from stdin')
    p = sub.add_parser("sync", help="diff my tickets against the stored snapshot")
    p.add_argument("--json", action="store_true")
    p.add_argument("--snapshot", default=str(tickets_dir / "snapshot.json"))
    p = sub.add_parser("board", help="status of every open ticket from ~/.aoa/tickets files (no Jira call)")
    p.add_argument("keys", nargs="*", help="only these keys (also closed or gone ones)")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--md", action="store_true", help="markdown table for the brief")
    p.add_argument("--all", action="store_true", help="every key with a file in the tickets folder")
    p.add_argument("--sync", action="store_true", help="run sync first and print what changed")
    p.add_argument("--watch", type=int, metavar="SECONDS", help="redraw every SECONDS (min 60 with --sync)")
    p.add_argument("--wiki", action="store_true", help="write wiki/tickets.md from all keys")
    p.add_argument("--tickets", default=str(tickets_dir))
    args = parser.parse_args(argv)

    try:
        if args.cmd == "board":
            try:
                return run_board(args, env)
            except KeyboardInterrupt:  # leaving --watch
                return 0
        cfg = load_config(env)
        if args.cmd == "mytickets":
            rows = my_tickets(cfg)
            print(json.dumps(rows, indent=2) if args.json else format_table(rows))
        elif args.cmd == "get":
            issues = [get_issue(cfg, k, comments=args.comments) for k in args.keys]
            print(json.dumps(issues, indent=2) if args.json
                  else "\n\n".join(format_issue(i) for i in issues))
        elif args.cmd == "comment":
            print(add_comment(cfg, args.key, args.text))
        else:
            diff = sync(cfg, args.snapshot)
            print(json.dumps(diff, indent=2) if args.json else format_sync(diff))
    except CliError as e:
        print(e, file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())

import io
import json
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

import main

ENV = {
    "JIRA_BASE_URL": "https://acme.atlassian.net",
    "JIRA_EMAIL": "me@acme.com",
    "JIRA_API_TOKEN": "tok",
}


class FakeHTTP:
    """Replaces main.urlopen. Serves queued JSON bodies, records requests."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())


def http_error(status, body):
    return urllib.error.HTTPError(
        "https://acme.atlassian.net/x", status, "err", {}, io.BytesIO(json.dumps(body).encode())
    )


def issue(key, summary="S", status="To Do", priority="High", itype="Task", desc="D", labels=()):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "priority": {"name": priority},
            "issuetype": {"name": itype},
            "description": desc,
            "labels": list(labels),
        },
    }


@pytest.fixture
def fake(monkeypatch):
    def install(*bodies):
        f = FakeHTTP(bodies)
        monkeypatch.setattr(main, "urlopen", f)
        return f
    return install


# --- config ---------------------------------------------------------------

def test_missing_env_var_exits_2_with_message(capsys):
    env = dict(ENV)
    del env["JIRA_API_TOKEN"]
    assert main.main(["mytickets"], env) == 2
    assert "JIRA_API_TOKEN" in capsys.readouterr().err


# --- request plumbing -----------------------------------------------------

def test_request_uses_basic_auth_and_json_accept(fake):
    f = fake({"issues": [], "isLast": True})
    main.main(["mytickets"], ENV)
    req = f.requests[0]
    assert req.get_header("Authorization") == "Basic bWVAYWNtZS5jb206dG9r"
    assert req.get_header("Accept") == "application/json"


def test_http_error_prints_jira_messages_and_exits_1(fake, capsys):
    fake(http_error(401, {"errorMessages": ["Unauthorized", "Bad token"]}))
    assert main.main(["mytickets"], ENV) == 1
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "Unauthorized" in err and "Bad token" in err


# --- mytickets ------------------------------------------------------------

def test_mytickets_queries_open_issues_assigned_to_me(fake):
    f = fake({"issues": [], "isLast": True})
    main.main(["mytickets"], ENV)
    url = urlparse(f.requests[0].full_url)
    assert url.path == "/rest/api/2/search/jql"
    q = parse_qs(url.query)
    assert q["jql"] == [
        "assignee = currentUser() AND statusCategory != Done ORDER BY priority ASC, updated DESC"
    ]
    assert q["fields"] == ["summary,status,priority,updated"]


def test_mytickets_follows_next_page_token(fake):
    f = fake(
        {"issues": [issue("A-1")], "nextPageToken": "p2"},
        {"issues": [issue("A-2")], "isLast": True},
    )
    main.main(["mytickets", "--json"], ENV)
    assert len(f.requests) == 2
    assert parse_qs(urlparse(f.requests[1].full_url).query)["nextPageToken"] == ["p2"]


def test_mytickets_json_output(fake, capsys):
    fake({"issues": [issue("A-1", "Fix login", "In Progress", "High")], "isLast": True})
    main.main(["mytickets", "--json"], ENV)
    assert json.loads(capsys.readouterr().out) == [
        {
            "key": "A-1",
            "status": "In Progress",
            "priority": "High",
            "summary": "Fix login",
            "updated": "",
            "url": "https://acme.atlassian.net/browse/A-1",
        }
    ]


def test_mytickets_table_output(fake, capsys):
    fake({"issues": [issue("A-1", "Fix login", "In Progress", "High"),
                     issue("A-22", "Add tests", "To Do", "Low")], "isLast": True})
    main.main(["mytickets"], ENV)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["KEY", "STATUS", "PRIORITY", "SUMMARY"]
    assert lines[1].startswith("A-1 ") and lines[1].endswith("Fix login")
    assert lines[2].startswith("A-22") and lines[2].endswith("Add tests")


def test_mytickets_empty_prints_notice(fake, capsys):
    fake({"issues": [], "isLast": True})
    main.main(["mytickets"], ENV)
    assert "No open tickets" in capsys.readouterr().out


# --- get ------------------------------------------------------------------

def test_get_fetches_each_key_with_fields(fake):
    f = fake(issue("A-1"), issue("B-2"))
    main.main(["get", "A-1", "B-2"], ENV)
    paths = [urlparse(r.full_url).path for r in f.requests]
    assert paths == ["/rest/api/2/issue/A-1", "/rest/api/2/issue/B-2"]
    q = parse_qs(urlparse(f.requests[0].full_url).query)
    assert q["fields"] == ["summary,status,priority,issuetype,description,labels"]


def test_get_json_output(fake, capsys):
    fake(issue("A-1", "Fix login", "To Do", "High", "Bug", "Steps...", ["auth"]))
    main.main(["get", "A-1", "--json"], ENV)
    assert json.loads(capsys.readouterr().out) == [
        {
            "key": "A-1",
            "type": "Bug",
            "status": "To Do",
            "priority": "High",
            "labels": ["auth"],
            "summary": "Fix login",
            "description": "Steps...",
            "url": "https://acme.atlassian.net/browse/A-1",
        }
    ]


def test_get_markdown_output(fake, capsys):
    fake(issue("A-1", "Fix login", "To Do", "High", "Bug", "Steps...", ["auth"]))
    main.main(["get", "A-1"], ENV)
    out = capsys.readouterr().out
    assert out.startswith("# A-1: Fix login")
    assert "Type: Bug" in out and "Status: To Do" in out and "Priority: High" in out
    assert "Labels: auth" in out
    assert "https://acme.atlassian.net/browse/A-1" in out
    assert "Steps..." in out


def test_get_comments_adds_thread(fake, capsys):
    thread = {"comments": [
        {"id": "7", "author": {"displayName": "Ann"}, "created": "2026-10-14T09:00:00.000+0200", "body": "Answer 1"},
    ]}
    f = fake(issue("A-1"), thread)
    main.main(["get", "A-1", "--comments", "--json"], ENV)
    paths = [urlparse(r.full_url).path for r in f.requests]
    assert paths == ["/rest/api/2/issue/A-1", "/rest/api/2/issue/A-1/comment"]
    out = json.loads(capsys.readouterr().out)[0]
    assert out["comments"] == [{"id": "7", "author": "Ann", "created": "2026-10-14T09:00:00.000+0200", "body": "Answer 1"}]


def test_get_comments_markdown_lists_each_comment(fake, capsys):
    thread = {"comments": [{"id": "7", "author": {"displayName": "Ann"}, "created": "2026-10-14T09:00:00.000+0200", "body": "Answer 1"}]}
    fake(issue("A-1"), thread)
    main.main(["get", "A-1", "--comments"], ENV)
    out = capsys.readouterr().out
    assert "## Ann (2026-10-14 09:00, #7)" in out and "Answer 1" in out


def test_get_without_comments_flag_makes_no_comment_request(fake, capsys):
    f = fake(issue("A-1"))
    main.main(["get", "A-1", "--json"], ENV)
    assert len(f.requests) == 1
    assert "comments" not in json.loads(capsys.readouterr().out)[0]


def test_get_handles_null_description_and_priority(fake, capsys):
    body = issue("A-1")
    body["fields"]["description"] = None
    body["fields"]["priority"] = None
    fake(body)
    main.main(["get", "A-1", "--json"], ENV)
    data = json.loads(capsys.readouterr().out)[0]
    assert data["description"] == ""
    assert data["priority"] == ""


def test_network_error_prints_reason_and_exits_1(fake, capsys):
    fake(urllib.error.URLError("nodename nor servname provided"))
    assert main.main(["mytickets"], ENV) == 1
    err = capsys.readouterr().err
    assert "nodename nor servname" in err and "Traceback" not in err


# --- .env -----------------------------------------------------------------

def test_load_dotenv_fills_missing_vars_without_overriding(tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\nJIRA_BASE_URL=https://dotenv.atlassian.net\n"
        "JIRA_EMAIL='quoted@acme.com'\n\nJIRA_API_TOKEN=\"t\"\n"
    )
    env = {"JIRA_EMAIL": "shell@acme.com"}
    main.load_dotenv(tmp_path / ".env", env)
    assert env == {
        "JIRA_BASE_URL": "https://dotenv.atlassian.net",
        "JIRA_EMAIL": "shell@acme.com",
        "JIRA_API_TOKEN": "t",
    }


def test_load_dotenv_missing_file_is_noop(tmp_path):
    env = {}
    main.load_dotenv(tmp_path / ".env", env)
    assert env == {}


def test_mytickets_includes_updated_timestamp(fake, capsys):
    body = issue("A-1")
    body["fields"]["updated"] = "2026-10-11T09:30:00.000+0200"
    fake({"issues": [body], "isLast": True})
    main.main(["mytickets", "--json"], ENV)
    row = json.loads(capsys.readouterr().out)[0]
    assert row["updated"] == "2026-10-11T09:30:00.000+0200"


def test_mytickets_requests_updated_field(fake):
    f = fake({"issues": [], "isLast": True})
    main.main(["mytickets"], ENV)
    q = parse_qs(urlparse(f.requests[0].full_url).query)
    assert q["fields"] == ["summary,status,priority,updated"]


# --- sync -----------------------------------------------------------------

def search_page(*issues):
    return {"issues": list(issues), "isLast": True}


def listed(key, status="To Do", updated="2026-10-10T10:00:00.000+0200", summary="S"):
    it = issue(key, summary=summary, status=status)
    it["fields"]["updated"] = updated
    return it


def detailed(key, status="To Do", updated="2026-10-10T10:00:00.000+0200", desc="D", summary="S"):
    it = issue(key, summary=summary, status=status, desc=desc)
    it["fields"]["updated"] = updated
    return it


def test_sync_first_run_reports_all_new_and_writes_snapshot(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    fake(search_page(listed("A-1")), detailed("A-1"))
    assert main.main(["sync", "--snapshot", str(snap), "--json"], ENV) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["new"] == ["A-1"] and out["changed"] == [] and out["gone"] == []
    stored = json.loads(snap.read_text())
    assert stored["A-1"]["description"] == "D"
    assert stored["A-1"]["updated"] == "2026-10-10T10:00:00.000+0200"


def test_sync_unchanged_fetches_no_details(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"A-1": {"key": "A-1", "status": "To Do", "summary": "S",
                                        "updated": "2026-10-10T10:00:00.000+0200"}}))
    f = fake(search_page(listed("A-1")))
    main.main(["sync", "--snapshot", str(snap), "--json"], ENV)
    assert len(f.requests) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"new": [], "gone": [], "changed": [], "touched": [], "unchanged": ["A-1"]}


def test_sync_reports_changed_fields_with_old_and_new(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"A-1": {"key": "A-1", "status": "To Do", "summary": "S",
                                        "description": "D", "priority": "High",
                                        "updated": "2026-10-10T10:00:00.000+0200"}}))
    fake(search_page(listed("A-1", status="In Progress", updated="2026-10-12T09:00:00.000+0200")),
         detailed("A-1", status="In Progress", updated="2026-10-12T09:00:00.000+0200", desc="D2"))
    main.main(["sync", "--snapshot", str(snap), "--json"], ENV)
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] == [{
        "key": "A-1",
        "fields": {"status": ["To Do", "In Progress"], "description": ["D", "D2"]},
    }]
    assert json.loads(snap.read_text())["A-1"]["status"] == "In Progress"


def test_sync_reports_gone_and_drops_from_snapshot(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"A-1": {"key": "A-1", "updated": "x"}}))
    fake(search_page())
    main.main(["sync", "--snapshot", str(snap), "--json"], ENV)
    assert json.loads(capsys.readouterr().out)["gone"] == ["A-1"]
    assert json.loads(snap.read_text()) == {}


def test_sync_text_output(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"A-1": {"key": "A-1", "status": "To Do", "summary": "S",
                                        "updated": "2026-10-10T10:00:00.000+0200"},
                                "B-2": {"key": "B-2", "updated": "x"}}))
    fake(search_page(listed("A-1", status="Done", updated="2026-10-12T09:00:00.000+0200"),
                     listed("C-3")),
         detailed("A-1", status="Done", updated="2026-10-12T09:00:00.000+0200"),
         detailed("C-3"))
    main.main(["sync", "--snapshot", str(snap)], ENV)
    out = capsys.readouterr().out
    assert "New: C-3" in out
    assert "Gone: B-2" in out
    assert "A-1 status: To Do -> Done" in out


def test_sync_text_output_nothing_changed(fake, capsys, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"A-1": {"key": "A-1", "updated": "2026-10-10T10:00:00.000+0200"}}))
    fake(search_page(listed("A-1")))
    main.main(["sync", "--snapshot", str(snap)], ENV)
    assert capsys.readouterr().out.strip() == "Nothing changed."


# --- comment --------------------------------------------------------------

def test_comment_posts_body_to_issue(fake, capsys):
    f = fake({"id": "10001"})
    assert main.main(["comment", "A-1", "Hallo\nWelt"], ENV) == 0
    req = f.requests[0]
    assert req.get_method() == "POST"
    assert urlparse(req.full_url).path == "/rest/api/2/issue/A-1/comment"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"body": "Hallo\nWelt"}
    assert "A-1" in capsys.readouterr().out


def test_comment_reads_body_from_stdin_when_dash(fake, monkeypatch):
    f = fake({"id": "1"})
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    main.main(["comment", "A-1", "-"], ENV)
    assert json.loads(f.requests[0].data) == {"body": "from stdin"}


def test_comment_refuses_empty_body(fake, capsys):
    f = fake({"id": "1"})
    assert main.main(["comment", "A-1", "  "], ENV) == 2
    assert f.requests == []


# --- sync: comment-only updates, timetracking buckets -------------------------------------------

def page_of(*rows):
    return {"issues": [{"key": k, "fields": {"summary": "S", "status": {"name": "To Do"},
                                             "priority": {"name": "High"}, "updated": u}}
                       for k, u in rows]}


def with_comments(key, *authors):
    it = issue(key)
    it["fields"]["comment"] = {"comments": [
        {"author": {"displayName": a}, "created": "2026-11-20T10:00:00.000+0100"} for a in authors]}
    return it


def test_sync_reports_comment_only_update_as_touched(fake, tmp_path):
    snap = tmp_path / "snapshot.json"
    fake(page_of(("A-1", "t1")), with_comments("A-1", "Ann"))
    main.sync(main.load_config(ENV), snap)
    fake(page_of(("A-1", "t2")), with_comments("A-1", "Ann", "Bob"))
    diff = main.sync(main.load_config(ENV), snap)
    assert diff["changed"] == [] and diff["unchanged"] == []
    assert diff["touched"] == [{"key": "A-1", "new_comments": 1, "last_comment": {
        "author": "Bob", "created": "2026-11-20T10:00:00.000+0100"}}]
    assert "A-1 1 new comment(s), last comment by Bob" in main.format_sync(diff)


def test_sync_skips_timetracking_buckets(fake, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"TIME-304": {"updated": "old"}}))
    f = fake(page_of(("TIME-304", "new")))
    diff = main.sync(main.load_config(ENV), snap)
    assert diff == {"new": [], "gone": [], "changed": [], "touched": [], "unchanged": []}
    assert len(f.requests) == 1
    assert json.loads(snap.read_text()) == {}


# --- board ----------------------------------------------------------------------------

PLAN = """# [{key}](https://acme.atlassian.net/browse/{key}): Title of {key}

Task · To Do · High · planned 2026-11-10

## Repo and files

- Repo: {repo}

## Open questions for the ticket writer

{questions}
"""


@pytest.fixture
def tdir(tmp_path):
    d = tmp_path / "tickets"
    d.mkdir()
    keys = ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "TIME-9"]
    (d / "snapshot.json").write_text(json.dumps({
        k: {"summary": f"Sum {k}", "status": "To Do", "priority": "High", "url": f"u/{k}"} for k in keys}))
    (d / "A-2.md").write_text(PLAN.format(key="A-2", repo="/r/x", questions="None."))
    (d / "A-3.md").write_text(PLAN.format(key="A-3", repo="/r/x", questions="1. Why?\n2. How?"))
    (d / "A-4.md").write_text(PLAN.format(key="A-4", repo="unknown, see open questions", questions="1. Which repo?"))
    (d / "A-5.md").write_text(PLAN.format(key="A-5", repo="/r/x", questions="None."))
    (d / "A-5.review.md").write_text("## Verdict: FIX\n\nround 2\n\n## Verdict: OK\n")
    (d / "A-6.md").write_text(PLAN.format(key="A-6", repo="/r/x", questions="None."))
    (d / "A-6.notes.md").write_text(
        "- 2026-11-09: repo: /r/x\n"
        "- 2026-11-12: REPORT pushed. Did it. Branch b, abc. Tests 3 passed. Next: test on stage.\n"
        "  Open: none.\n")
    (d / "TODO-1.notes.md").write_text("# TODO-1 — Old thing\n\n- 2026-11-01: (user) Done.\n- 2026-11-02: late fact\n")
    (d / "TODO-2.notes.md").write_text("- 2026-11-03: added. New thing to do. More text.\n")
    (d / "TODO-3.notes.md").write_text("# TODO-3 — Became a ticket\n\n- 2026-11-03: Jira key A-1 (url).\n"
                                       "- 2026-11-04: REPORT stage. X. Next: go live.\n")
    (d / "Z-9.md").write_text(PLAN.format(key="Z-9", repo="/r/x", questions="None."))
    return d


def test_board_qualifies_each_ticket(tdir, tmp_path):
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", home=tmp_path)}
    assert list(rows) == ["TODO-2", "A-1", "A-2", "A-3", "A-4", "A-5", "A-6"]
    assert {k: r["qual"] for k, r in rows.items()} == {
        "TODO-2": "-", "A-1": "REPORTED", "A-2": "READY", "A-3": "BLOCKED", "A-4": "NO-REPO",
        "A-5": "FEEDBACK", "A-6": "REPORTED"}
    assert rows["A-3"]["next"] == "Clarify 2 questions"
    assert rows["A-6"]["next"] == "test on stage"
    assert rows["A-6"]["report"] == {"date": "2026-11-12", "state": "pushed", "next": "test on stage"}
    assert rows["A-1"]["files_under"] == "TODO-3" and rows["A-1"]["next"] == "go live"
    assert rows["TODO-2"]["title"] == "New thing to do. More text."


def test_board_all_and_named_keys_include_closed_and_gone(tdir, tmp_path):
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", everything=True, home=tmp_path)}
    assert rows["TODO-1"]["qual"] == "DONE" and rows["TODO-1"]["title"] == "Old thing"
    assert rows["Z-9"]["in_jira"] is False and rows["Z-9"]["qual"] == "READY"
    assert "TIME-9" not in rows
    only = main.board(tdir, tdir / "snapshot.json", keys=["TODO-1"], home=tmp_path)
    assert [r["key"] for r in only] == ["TODO-1"]


def test_board_command_needs_no_credentials_and_prints_md(tdir, capsys):
    assert main.main(["board", "--md", "--tickets", str(tdir)], env={}) == 0
    out = capsys.readouterr().out
    assert "| [A-3](u/A-3) | Sum A-3 | To Do | High | BLOCKED | Clarify 2 questions |" in out
    assert "| TODO-2 | New thing to do. More text. | — | — | — |  |" in out


def test_board_wiki_writes_tickets_page(tdir, capsys):
    (tdir.parent / "wiki").mkdir()
    assert main.main(["board", "--wiki", "--tickets", str(tdir)], env={}) == 0
    page = (tdir.parent / "wiki" / "tickets.md").read_text()
    assert page.startswith("---\ntype: overview\n")
    assert "| TODO-1 | Old thing | TODO | done |" in page
    assert "| [A-6](u/A-6) | Sum A-6 | A | pushed | `/r/x` | 2026-11-12 | test on stage | plan, notes |" in page


def test_git_state_reads_branch_dirty_and_unpushed(tmp_path, monkeypatch):
    out = "# branch.oid abc\n# branch.head fix/A-1\n# branch.upstream origin/fix/A-1\n# branch.ab +2 -0\n1 .M N... a.py\n? new.txt\n"
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": out})())
    assert main.git_state(str(tmp_path)) == {"branch": "fix/A-1", "dirty": 2, "unpushed": 2}
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 128, "stdout": ""})())
    assert main.git_state(str(tmp_path)) is None


def test_aoa_home_default_and_override():
    assert main.aoa_home({}) == main.Path("~/.aoa").expanduser()
    assert main.aoa_home({"AOA_HOME": "/data/x"}) == main.Path("/data/x")


def test_board_closed_needs_the_user_or_a_done_report(tdir, tmp_path):
    notes = {"TODO-10": "- 2026-11-01: Done: migration script, rest still open\n",
             "TODO-11": "- 2026-11-01: closed question 2 with the writer\n",
             "TODO-12": "- 2026-11-01: closed by user: not ours\n",
             "TODO-13": "- 2026-11-01: DONE (user ran it 15:47): moved\n",
             "TODO-14": "- 2026-11-01: user said so. Status: done.\n",
             "TODO-15": "- 2026-11-01: REPORT done. Shipped.\n",
             "TODO-16": "- 2026-11-01: done (user).\n- 2026-11-02: reopened, still broken\n"}
    for key, text in notes.items():
        (tdir / f"{key}.notes.md").write_text(text)
    rows = {r["key"]: r["closed"] for r in main.board(tdir, tdir / "snapshot.json", everything=True, home=tmp_path)}
    assert {k: rows[k] for k in notes} == {"TODO-10": False, "TODO-11": False, "TODO-12": True, "TODO-13": True,
                                           "TODO-14": True, "TODO-15": True, "TODO-16": False}


def test_board_todo_without_plan_or_report_does_not_blank_its_jira_row(tdir, tmp_path):
    (tdir / "TODO-3.notes.md").write_text("- 2026-11-03: Jira key A-1 (url).\n")
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", home=tmp_path)}
    assert rows["A-1"]["qual"] == "UNPLANNED" and rows["A-1"]["next"] == "Plan it"


def test_board_named_key_gets_the_state_of_its_linked_todo(tdir, tmp_path):
    (row,) = main.board(tdir, tdir / "snapshot.json", keys=["A-1"], home=tmp_path)
    assert row["qual"] == "REPORTED" and row["files_under"] == "TODO-3"


def test_board_report_does_not_make_notes_newer_but_a_decision_does(tdir, tmp_path):
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", home=tmp_path)}
    assert rows["A-6"]["notes_newer"] is False
    with (tdir / "A-6.notes.md").open("a") as f:
        f.write("- 2026-11-13: writer says: only for admins\n")
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", home=tmp_path)}
    assert rows["A-6"]["notes_newer"] is True


def test_board_survives_broken_snapshot_and_odd_bytes_and_repo_without_colon(tdir, tmp_path):
    (tdir / "snapshot.json").write_text("{ broken")
    (tdir / "A-3.notes.md").write_bytes(b"- 2026-11-11: repo /r/y caf\xe9\n")
    rows = {r["key"]: r for r in main.board(tdir, tdir / "snapshot.json", everything=True, home=tmp_path)}
    assert rows["A-3"]["in_jira"] is False and "/r/y" in rows["A-3"]["repos"]


def test_sync_treats_broken_snapshot_as_empty_and_replaces_it(fake, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text("")
    fake(page_of(("A-1", "t1")), with_comments("A-1"))
    assert main.sync(main.load_config(ENV), snap)["new"] == ["A-1"]
    assert list(json.loads(snap.read_text())) == ["A-1"] and not (tmp_path / "snapshot.json.tmp").exists()


def test_sync_deleted_comment_is_not_a_negative_count(fake, tmp_path):
    snap = tmp_path / "snapshot.json"
    fake(page_of(("A-1", "t1")), with_comments("A-1", "Ann", "Bob"))
    main.sync(main.load_config(ENV), snap)
    fake(page_of(("A-1", "t2")), with_comments("A-1", "Ann"))
    assert "A-1 updated, no field change" in main.format_sync(main.sync(main.load_config(ENV), snap))


def test_dotenv_drops_inline_comment_and_fills_empty_values(tmp_path):
    f = tmp_path / ".env"
    f.write_text("AOA_SKIP_PREFIX=TIME-     # optional\nJIRA_EMAIL=me@acme.com\n")
    env = {"JIRA_EMAIL": ""}
    main.load_dotenv(f, env)
    assert env == {"AOA_SKIP_PREFIX": "TIME-", "JIRA_EMAIL": "me@acme.com"}


def test_watch_keeps_running_when_a_jira_check_fails(tdir, capsys, monkeypatch):
    monkeypatch.setattr(main, "sync", lambda *a: (_ for _ in ()).throw(main.CliError("Connection failed: x", 1)))
    def stop(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(main.time, "sleep", stop)
    assert main.main(["board", "--sync", "--watch", "60", "--tickets", str(tdir)], env=dict(ENV)) == 0
    assert "Jira check failed" in capsys.readouterr().out

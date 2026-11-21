#!/usr/bin/env bash
# Prepare this machine: private data folder outside the repo, memory location, global report skill.
# Safe to run again; it never overwrites existing files.
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
# AOA_HOME: shell variable, else the repo's .env (path setting only), else ~/.aoa
if [ -z "${AOA_HOME:-}" ] && [ -f "$repo/.env" ]; then
  AOA_HOME="$(sed -n 's/^AOA_HOME=//p' "$repo/.env" | tail -1 | sed -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']$//")"
fi
home="${AOA_HOME:-$HOME/.aoa}"; home="${home/#\~/$HOME}"

mkdir -p "$home"/{tickets,wiki,briefs,errors,memory}
[ -f "$home/tickets/repos.md" ] || printf '# Local clones\n\nOne line per clone: key prefix, path, remote, what it is.\n' > "$home/tickets/repos.md"
[ -f "$home/errors/index.md" ] || printf '# Production errors\n\nOne row per cause, newest first. Status: OPEN, NOISE, FIXED.\n\n| First seen | Site | Error | Status | Impact |\n|---|---|---|---|---|\n' > "$home/errors/index.md"
[ -f "$home/sites.md" ] || printf '# Known sites\n\nOne row per site for /aoa-error.\n\n| Log says | Server (ssh) | Docroot | drush | Log files | Code |\n|---|---|---|---|---|---|\n' > "$home/sites.md"
[ -f "$home/wiki/README.md" ] || cp "$repo/templates/wiki/README.md" "$home/wiki/README.md"
if [ ! -f "$home/.env" ]; then
  printf 'JIRA_BASE_URL=https://your-site.atlassian.net\nJIRA_EMAIL=\nJIRA_API_TOKEN=\n# AOA_SKIP_PREFIX=TIME-\n' > "$home/.env"
  chmod 600 "$home/.env"
  echo "created $home/.env: fill in the Jira values"
fi

# Claude's memory for this project: private, outside the repo (per machine, git-ignored file).
local_settings="$repo/.claude/settings.local.json"
python3 - "$local_settings" "$home/memory" <<'PY'
import json, sys
from pathlib import Path
p, mem = Path(sys.argv[1]), sys.argv[2].replace(str(Path.home()), "~")
d = json.loads(p.read_text()) if p.is_file() else {}
if "autoMemoryDirectory" not in d:
    d["autoMemoryDirectory"] = mem
    p.write_text(json.dumps(d, indent=2) + "\n")
PY

# /aoa-report must work from ticket sessions in other repos.
mkdir -p "$HOME/.claude/skills"
link="$HOME/.claude/skills/aoa-report"
if [ -L "$link" ] || [ ! -e "$link" ]; then
  ln -sfn "$repo/.claude/skills/aoa-report" "$link"
else
  echo "kept $link: it is a real folder, not a link. Remove it yourself if /aoa-report should come from this repo."
fi

echo "private data: $home"
echo "next: uv sync && uv run main.py board"

# kule

<!-- TODO: screenshot to be added -->

[English](README.en.md) · Türkçe

## Description

kule is a control tower that collects the status of Umut's projects into a
single panel. Git repository state, cor (claude-openrouter) proxy health, the
last run of the BorsaSite pipeline, the readbunny database state, the hygiene
of the Mt3Ui55OS vault, and automatic maintenance warnings (full disks,
forgotten processes, stale uncommitted changes) — all of it on one dark-themed
web page that refreshes itself every 30 seconds.

Not offline-first: kule does not produce data on its own. It connects to six
different sources (filesystem, HTTP, Postgres) and reads from them. If one
source is unreachable the panel does not crash — that source's card just
shows "unreachable" and the others are unaffected. The same summary can be
taken once with `kule --notify-once` and sent to Telegram as a warning if
something is wrong. The panel is a single HTML page with no framework and no
build step: CSS and JS are embedded, there are no frontend dependencies.

## Install

```bash
pip install -e .
cp config.yaml.example config.yaml
```

Fill in `config.yaml` with your own paths/URLs (every field is explained
below). `config.yaml` is in `.gitignore` — it is never committed and stays
local to each machine.

## Run

```bash
kule                      # starts the server (127.0.0.1:8790)
kule --port 8000 --reload # extra options
uvicorn app.main:app      # running uvicorn directly works too
```

The panel is served at `http://127.0.0.1:8790`. If `config.yaml` is missing
the server still starts, but `/` and `/api/summary` return 500 telling you to
copy `config.yaml.example` (so that things like a reverse proxy health check,
which must work independently of config, keep working — the server chooses
this path rather than not starting at all).

### One-off status notification

```bash
kule --notify-once
```

It does **not** start a server; it reads the six sources once and, if any of
them is unreachable or erroring — or if automatic maintenance found
something — sends a warning to Telegram and prints it to stdout. If there is
no problem it prints `kule: her şey yolunda` and sends nothing. It exits with
code `0` in every case — in scheduled jobs like cron, "there is a problem" is
not treated as an error:

```cron
*/15 * * * * /path/to/venv/bin/kule --notify-once
```

`telegram.bot_token`/`chat_id` must be filled in for delivery; if they are
empty no request is ever made to Telegram and the warning only goes to stdout.

## config.yaml fields

```yaml
repo_roots:              # root directories to scan for git repositories
  - ~/Desktop             # ~ and $ENV_VAR are expanded
  - ~/Documents           # at most 4 levels deep are descended per root

cor:
  base_url: "http://127.0.0.1:8787"   # address cor is running at

borsasite:
  health_url: "https://.../api/health"  # BorsaSite's health endpoint
  database_url: ""    # optional — if empty only the HTTP health is used;
                      # if filled in, the last run time is also read from the
                      # trade_decisions/predictions tables

readbunny:
  database_url: "postgresql://..."   # readbunny's Postgres connection
                                     # (a summary is read from the links table)

vault:
  path: "/home/user/Mt3Ui55OS"   # local path of the Mt3Ui55OS vault

maintenance:                    # all OPTIONAL — missing fields fall back to defaults
  disk_threshold_percent: 90    # disks fuller than this percentage are warned about
  stale_process_hours: 6       # processes running longer than this count as "forgotten"
  stale_git_days: 3             # a dirty repo is warned about if its change is older than this
  process_names: [ollama, uvicorn, node]  # process name patterns to watch
  # disk_paths: []              # if empty, repo_roots is used; if that is empty too, "/"

telegram:
  bot_token: ""   # `kule --notify-once` sends warnings with this
  chat_id: ""     # can also be read from env: KULE_TELEGRAM_BOT_TOKEN / KULE_TELEGRAM_CHAT_ID
```

You don't have to write Telegram secrets into `config.yaml` — the environment
variable takes precedence over the yaml value, so secrets never enter the
repository.

## Endpoints

- **`GET /`** — the panel itself (a single HTML page with embedded CSS/JS, no
  build step). On page load and every 30 seconds it fetches `/api/summary`
  and fills the DOM from the returned JSON. If a request fails, the last known
  data stays on screen and only a quiet "connection lost" badge is shown.
- **`GET /api/summary`** — the JSON summary of the six sources:

  ```json
  {
    "git": [{"name", "path", "branch", "dirty_count", "son_commit"}, ...],
    "cor": {"reachable", "health", "dashboard_health", "metrics"},
    "borsasite": {"reachable", "health", "db": {"last_trade_decision", "last_prediction"}},
    "readbunny": {"reachable", "last_updated", "error_count", "pending_count", "total_count"},
    "vault": {"broken_link_count", "orphan_note_count", "open_threads", "total_threads"},
    "maintenance": {
      "disk": {"threshold_percent", "full": [{"path", "percent", "free_gb"}]},
      "stale_processes": {"min_hours", "names", "items": [{"pid", "name", "hours"}]},
      "stale_git": {"min_days", "items": [{"name", "dirty_count", "age_days"}]}
    },
    "collected_at": 1234567890.0
  }
  ```

  Each source is isolated in its own try/except; if one blows up its key
  becomes `{"error": "..."}` and the others are unaffected. The result is
  cached in process memory for 60 seconds (rapid successive requests do not
  re-trigger the real collectors).

`kule --notify-once` reads this summary and turns the broken sources into a
short Turkish warning text (`app/notifier.py::build_alert_message`): `cor` is
unreachable, `vault` is erroring, 2 repos in `git` could not be read, etc.
`collected_at` is not a source and produces no warning.

### The maintenance card in the panel

The same `maintenance` data is visible in the panel: the **"bakım bulgusu"**
(maintenance finding) counter between the boxes at the top (total of full
disks + forgotten processes + stale uncommitted repos, with the thresholds
below it), and at the bottom the **bakım** (maintenance) card — full disks,
forgotten processes and stale uncommitted repos each in their own table. When
there are no findings it stays in a calm "all good" state; if one subsection
cannot be read (e.g. processes when `psutil` isn't installed) its badge turns
red while the other two lists keep rendering normally. A finding also moves
the status dot in the top bar to "there is a point that needs attention" — the
panel and the Telegram warning show the same finding with the same severity.

## Automatic maintenance bot (Wave F)

The `maintenance` source automatically detects three things — it only
**reports**, it never intervenes (no process is killed, no file is deleted, no
git command is run):

- **Disk fullness** — `shutil.disk_usage` checks `disk_paths` (or `repo_roots`
  if unset, or `/` if that is empty too); those above `disk_threshold_percent`
  are listed.
- **Forgotten processes** — `psutil` lists processes matching the
  `process_names` pattern that have been running longer than
  `stale_process_hours`. If `psutil` isn't installed only this section returns
  `error`.
- **Stale uncommitted change** — since `git_status` already scans the repos,
  that output is reused: repos that are **dirty** are listed if their most
  recent file modification is older than `stale_git_days`.

The `--notify-once` output adds these findings to the other sources in the
same Turkish warning text, each on its own line:

```
⚠️ kule uyarısı: cor erişilemiyor (kapalı)
disk dolu: / (%95.2)
uzun süredir çalışan süreçler: ollamax2, uvicorn
eski commitlenmemiş değişiklik: kule, borsa
🕐 2026-09-28 10:20
```

If there is no maintenance finding (and no broken source) no message is
produced and `kule: her şey yolunda` is printed.

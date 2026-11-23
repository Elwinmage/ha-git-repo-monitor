# ha-git-repo-monitor

🇬🇧 English | [🇫🇷 Français](doc/fr/README.md) | [🇩🇪 Deutsch](doc/de/README.md) | [🇪🇸 Español](doc/es/README.md) | [🇮🇹 Italiano](doc/it/README.md) | [🇵🇱 Polski](doc/pl/README.md) | [🇵🇹 Português](doc/pt/README.md)

An overview of all your git repositories in Home Assistant: local state, GitHub Actions, releases, stars and active installs. It is refreshed **only when something changes**: a commit, a push or the end of a workflow.

![Card](doc/img/card.png)

The project has two parts in one repository:

- **a daemon** (Python) running on the machine that holds your working copies. It publishes everything over MQTT with Home Assistant discovery;
- **a Lovelace card** (`custom:git-repo-monitor-card`), installed through HACS.

## How it works

| Trigger | What is refreshed |
|---|---|
| Change in `.git` (inotify): commit, checkout, merge, fetch… | local state of that repository, about 2 s later |
| Every `status_interval` (90 s) | uncommitted changes, coverage report |
| GitHub webhook (optional) | GitHub state of the repository, then "hot" mode |
| Your GitHub activity feed, every 60 s (free conditional requests) | repositories you pushed to or released |
| "Hot" mode, every 20 s | after a push: fetch + workflows, until every run is finished |
| Every 20 min | full refresh (safety net) |
| Once a day | active installs (analytics.home-assistant.io), daily star baseline |

Local data: branch, upstream, commits to push/pull, uncommitted changes, last tag, last commit, version from `manifest.json` or `package.json`, coverage read from `reports/coverage.xml` (Cobertura format: the tests are never run by the daemon).

GitHub data: latest run of each workflow, latest release and commits since, open pull requests (Dependabot ones counted separately), issues, stars and forks.

Active installs: taken from the public file of Home Assistant analytics, for repositories containing an integration (`custom_components/<domain>/manifest.json`). Only users who enabled usage analytics are counted; cards and blueprints have no figure.

## Installation

### 1. The daemon

Copy [`config.example.yaml`](config.example.yaml) to `config.yaml` and adapt it (MQTT broker, directories holding the repositories).

**Docker** (recommended) — see [`deploy/docker-compose.yml`](deploy/docker-compose.yml):

```bash
cd deploy
cp ../config.example.yaml config.yaml   # then edit it
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Mount the repositories at the same path as on the host. Run the container with the uid that owns the repositories (`user:`), otherwise `git fetch` leaves files your account cannot modify.

**Without Docker** (Python ≥ 3.12): `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, then the systemd user unit [`deploy/git-repo-monitor.service`](deploy/git-repo-monitor.service).

### 2. The card

HACS → three dots → *Custom repositories* → `https://github.com/Elwinmage/ha-git-repo-monitor`, type **Dashboard**. Then add the *Git Repo Monitor* card: it finds the repositories by itself.

## GitHub token

Create a *fine-grained* token on your repositories with the permissions **Metadata** (read), **Contents** (read), **Actions** (read and write: write is only needed for *Re-run failed*) and **Pull requests** (read). Give it through `GITHUB_TOKEN`.

The same token is used for `git fetch`: SSH remotes (`git@github.com:…`) are rewritten to HTTPS for that command, so no SSH key is needed in the container. Without a token, only the local state is published.

## Real-time webhooks (optional)

Without webhooks, a push from another machine or the end of a scheduled run is seen within 20 minutes at most. With them, it is seen within seconds.

1. Enable `webhook` in `config.yaml` with a secret (or `GRM_WEBHOOK_SECRET`).
2. Expose the daemon port through your reverse proxy, over HTTPS. On a Synology: *Control Panel → Login Portal → Advanced → Reverse Proxy*, source `https://your.domain:443` with a dedicated host name or port, destination `http://<machine>:8099`. Only `POST /webhook` and `GET /health` answer, and every request is checked with the HMAC signature.
3. Create the webhooks (GitHub CLI with the `admin:repo_hook` scope):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://your.domain/webhook            # all your repositories
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://your.domain/webhook owner/repo # some of them
```

The script can be run again: it updates the existing webhooks. Run it again when you create a repository.

## Home Assistant entities

One device per repository (`<host>` is `host_id`, `<repo>` the directory name):

| Entity | Content |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; every detail as attributes |
| `binary_sensor.git_<repo>_ci_problem` | a workflow failed (for your notifications) |
| `binary_sensor.git_<repo>_needs_sync` | uncommitted changes or commits to push/pull |
| `sensor.git_<repo>_version` | version in the working copy |
| `sensor.git_<repo>_stars` | stars (long-term statistics) |
| `sensor.git_<repo>_active_installs` | active installs (integrations only) |
| `sensor.git_<repo>_coverage` | coverage in % (if a report exists) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | commands |

Plus one daemon device: number of repositories, GitHub API quota left, *Refresh all* button. Every entity becomes unavailable when the daemon stops (MQTT last will).

## Card options

```yaml
type: custom:git-repo-monitor-card
title: My repositories     # optional
host: alare                # only this machine (default: all)
sort: status               # status (problems first) | name
show_ok: true              # show up-to-date repositories
expand_problems: false     # expand repositories whose CI failed
compact: false             # one line per repository
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Everything can be set in the visual editor. Click a row to see the failed workflows, the last commit, the latest release, the installs per version and the *Refresh*, *Fetch* and *Re-run failed* buttons.

## MQTT topics

| Topic | Content |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retained, last will) |
| `git_repo_monitor/<host>/<repo>/state` | JSON state of the repository (retained) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | daemon summary |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Development

```bash
pip install -e ".[test]" && pytest     # 100 % coverage required, report in reports/
cd card && npm install && npm run build
```

Releases are made with the *Release* workflow (version number as input): it updates the versions, builds the card attached to the GitHub release and publishes the `ghcr.io/elwinmage/ha-git-repo-monitor` image.

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

This tutorial goes from an empty machine to a working card. Count about twenty minutes.

Before you start, you need: the machine holding your working copies, with **Docker** or **Python ≥ 3.12**; an **MQTT broker** already paired with Home Assistant (the Mosquitto add-on is perfect) and the **MQTT** integration configured; **HACS** installed. A GitHub token is optional — without one the card still shows the local state.

### Step 1 — Get MQTT credentials

The daemon logs in to the broker like any other client. With the Mosquitto add-on, the simplest way is a dedicated Home Assistant user: *Settings → People → Add person*, turn *Allow person to login* on, call it `git_monitor`. Note the broker address (the Home Assistant machine, port 1883 by default) and that login/password.

### Step 2 — Create a GitHub token (recommended)

On GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories**, or only those you monitor;
- *Permissions*: **Metadata** (read), **Contents** (read), **Actions** (read **and write** — write is only needed for *Re-run failed*), **Pull requests** (read).

Copy the `github_pat_…` value right away, GitHub never shows it again. That same token is used for `git fetch`: SSH remotes (`git@github.com:…`) are rewritten to HTTPS for that command, so the container needs no SSH key. Without a token the daemon logs `No GitHub token: only the local state is monitored` and publishes the local state only.

### Step 3 — Write the configuration

On the machine holding the repositories:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Three keys to adapt in `config.yaml`, the rest can stay as it is:

```yaml
host_id: alare                       # name of this machine in HA (default: the hostname)
mqtt:
  host: 192.168.0.109                # your broker (step 1)
  username: git_monitor              # the password comes from the environment, step 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # directory holding your working copies
```

Every git working copy directly below `base_dirs` is monitored (`depth: 1`; raise it for repositories grouped in sub-directories). Secrets stay out of the file: they are read from the environment, and [`config.example.yaml`](config.example.yaml) documents every other key.

### Step 4 — Start the daemon

**With Docker** (recommended). Still in `deploy/`, put the secrets in a `.env` file next to `docker-compose.yml`, and find the uid/gid owning the repositories:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Then edit [`deploy/docker-compose.yml`](deploy/docker-compose.yml): those uid/gid in `user:`, and your `base_dirs` mounted **at the same path inside and outside** the container (the card then shows the real host path). Running with another uid is the classic mistake: `git fetch` would leave behind files your own account cannot modify.

```bash
docker compose up -d
docker compose logs -f
```

**Without Docker** (Python ≥ 3.12), with the systemd user unit [`deploy/git-repo-monitor.service`](deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # keep it running with no session open
journalctl --user -u git-repo-monitor -f
```

Either way, the logs should show one `Repository <name> found at <path>` line per repository, then `Watching N git directories`. A `Configuration error: …` line means `config.yaml` was refused; the table at the end of this section lists the usual causes.

### Step 5 — Check in Home Assistant

*Settings → Devices & services → MQTT → devices*: one device per repository, plus one for the daemon itself. Open one of them — `sensor.git_<repo>_status` should already hold `ok`, `needs_sync`, `ci_failed`… If nothing appears there while the logs are clean, Home Assistant is not reading the broker the daemon writes to.

### Step 6 — Install the card

*HACS → three dots (top right) → Custom repositories*: URL `https://github.com/Elwinmage/ha-git-repo-monitor`, type **Dashboard**, *Add*. Then open *Git Repo Monitor card* and *Download* it. Reload the page (`Ctrl-Shift-R`) so the browser picks up the new file.

### Step 7 — Add the card to a dashboard

*Edit dashboard → Add card*, then search for *Git Repo Monitor*. It finds the repositories by itself; everything else is optional and can be set in the visual editor, see [Card options](#card-options).

Installation is done. The next step is optional: it makes the GitHub side of the card react in seconds instead of minutes.

### Step 8 (optional) — Real-time webhooks

Without webhooks, a push from another machine or the end of a scheduled run is seen within 20 minutes at worst. With them, within seconds.

1. In `config.yaml`, set `webhook.enabled: true`, add the secret to `GRM_WEBHOOK_SECRET` (in `.env` or in the `env` file) and restart the daemon: the log shows `Webhook listening on 0.0.0.0:8099/webhook`.
2. Expose that port through your reverse proxy, over HTTPS. On a Synology: *Control Panel → Login Portal → Advanced → Reverse Proxy*, source `https://your.domain:443` with a dedicated host name or port, destination `http://<machine>:8099`. Only `POST /webhook` and `GET /health` answer, and every request is checked against its HMAC signature — `curl https://your.domain/health` should answer `ok`.
3. Create the webhooks with the GitHub CLI (`gh auth login`, with the `admin:repo_hook` scope):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://your.domain/webhook            # all your repositories
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://your.domain/webhook owner/repo # some of them
```

The script can be run again: it updates the existing webhooks. Run it again when you create a repository. On GitHub, *Settings → Webhooks* of a repository shows each delivery and its answer (`202 accepted`).

### If something goes wrong

| Symptom | Where to look |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | wrong file read (`-c`), or `base_dirs` left empty |
| `MQTT connection lost: …` over and over | broker address, username, `GRM_MQTT_PASSWORD` |
| No device in Home Assistant although the logs are clean | **MQTT** integration missing, or a `discovery_prefix` other than `homeassistant` |
| `Cannot list <directory>` | wrong `base_dirs`, or a directory the daemon's uid cannot read |
| No repository found | working copies deeper than `depth`, or an `exclude` pattern too wide |
| Repositories published, but no GitHub data | no token (`No GitHub token` in the logs) or missing permissions |
| `git fetch failed for <repo>` | token without **Contents** (read), or a private repository outside its scope |
| `dubious ownership` in the logs | container started with an uid other than the owner of the repositories |
| *Git Repo Monitor* missing from the card list | HACS download unfinished, or page not reloaded |

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
repos:                     # only these repositories, in this order (default: all of them)
  - alare/ha-reef-card
  - alare/ha-reefbeat-component
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

Everything can be set in the visual editor, the list of repositories included: add the ones you want, then move them up or down until the order suits you. An empty list shows them all, sorted by `sort`; a list set here wins over `sort`. An entry can also be a bare directory name (`ha-reef-card`) to match it on every host. Click a row to see the failed workflows, the last commit, the latest release, the installs per version and the *Refresh*, *Fetch* and *Re-run failed* buttons.

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

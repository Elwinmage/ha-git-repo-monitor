# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | [🇫🇷 Français](../fr/README.md) | 🇩🇪 Deutsch | [🇪🇸 Español](../es/README.md) | [🇮🇹 Italiano](../it/README.md) | [🇵🇱 Polski](../pl/README.md) | [🇵🇹 Português](../pt/README.md)

Ein Überblick über alle Ihre Git-Repositories in Home Assistant: lokaler Zustand, GitHub Actions, Releases, Sterne und aktive Installationen. Er wird **nur aktualisiert, wenn sich etwas ändert**: ein Commit, ein Push oder das Ende eines Workflows.

![Karte](../img/card.png)

Das Projekt besteht aus zwei Teilen in einem Repository:

- **ein Dienst** (Python), der auf dem Rechner mit Ihren Arbeitskopien läuft. Er veröffentlicht alles per MQTT mit der automatischen Erkennung von Home Assistant;
- **eine Lovelace-Karte** (`custom:git-repo-monitor-card`), installiert über HACS.

## Funktionsweise

| Auslöser | Was aktualisiert wird |
|---|---|
| Änderung in `.git` (inotify): Commit, Checkout, Merge, Fetch… | lokaler Zustand des Repositorys, etwa 2 s später |
| Alle `status_interval` (90 s) | nicht committete Änderungen, Abdeckungsbericht |
| GitHub-Webhook (optional) | GitHub-Zustand des Repositorys, dann „heißer" Modus |
| Ihr GitHub-Aktivitätsfeed, alle 60 s (kostenlose bedingte Anfragen) | Repositories, in die Sie gepusht oder ein Release veröffentlicht haben |
| „Heißer" Modus, alle 20 s | nach einem Push: Fetch + Workflows, bis alle Läufe beendet sind |
| Alle 20 min | vollständige Aktualisierung (Sicherheitsnetz) |
| Einmal täglich | aktive Installationen (analytics.home-assistant.io), tägliche Sterne-Referenz |

Lokale Daten: Branch, Upstream, Commits zum Pushen/Pullen, nicht committete Änderungen, letzter Tag, letzter Commit, Version aus `manifest.json` oder `package.json`, Abdeckung aus `reports/coverage.xml` (Cobertura-Format: der Dienst führt die Tests nie aus).

GitHub-Daten: letzter Lauf jedes Workflows, letztes Release und Commits seitdem, offene Pull Requests (die von Dependabot getrennt gezählt), Issues, Sterne und Forks.

Aktive Installationen: aus der öffentlichen Statistikdatei von Home Assistant, für Repositories mit einer Integration (`custom_components/<domain>/manifest.json`). Gezählt werden nur Nutzer, die die Nutzungsstatistiken aktiviert haben; Karten und Blueprints haben keine Zahl.

## Installation

Dieses Tutorial führt von einer leeren Maschine bis zur funktionierenden Karte. Rechnen Sie mit etwa zwanzig Minuten.

Vorausgesetzt werden: die Maschine mit Ihren Arbeitskopien, mit **Docker** oder **Python ≥ 3.12**; ein **MQTT-Broker**, der bereits mit Home Assistant verbunden ist (das Mosquitto-Add-on genügt völlig) und die Integration **MQTT** eingerichtet; **HACS** installiert. Das GitHub-Token ist optional: ohne Token zeigt die Karte trotzdem den lokalen Zustand.

### Schritt 1 — MQTT-Zugang besorgen

Der Dienst meldet sich am Broker an wie jeder andere Client. Beim Mosquitto-Add-on ist ein eigener Home-Assistant-Benutzer am einfachsten: *Einstellungen → Personen → Person hinzufügen*, *Anmeldung erlauben* aktivieren, `git_monitor` nennen. Notieren Sie die Broker-Adresse (die Home-Assistant-Maschine, Port 1883 als Standard) und dieses Benutzername/Passwort-Paar.

### Schritt 2 — GitHub-Token erstellen (empfohlen)

Auf GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories** oder nur die überwachten;
- *Permissions*: **Metadata** (lesen), **Contents** (lesen), **Actions** (lesen **und schreiben** — schreiben wird nur für *Fehlgeschlagene neu starten* gebraucht), **Pull requests** (lesen).

Kopieren Sie den Wert `github_pat_…` sofort, GitHub zeigt ihn nie wieder an. Dasselbe Token dient `git fetch`: SSH-Remotes (`git@github.com:…`) werden für diesen Befehl auf HTTPS umgeschrieben, ein SSH-Schlüssel im Container ist also nicht nötig. Ohne Token schreibt der Dienst `No GitHub token: only the local state is monitored` ins Log und veröffentlicht nur den lokalen Zustand.

### Schritt 3 — Konfiguration schreiben

Auf der Maschine mit den Repositories:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Drei Schlüssel in `config.yaml` anpassen, der Rest kann so bleiben:

```yaml
host_id: alare                       # Name dieser Maschine in HA (Standard: der Hostname)
mqtt:
  host: 192.168.0.109                # Ihr Broker (Schritt 1)
  username: git_monitor              # das Passwort kommt aus der Umgebung, Schritt 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # Verzeichnis mit Ihren Arbeitskopien
```

Jede git-Arbeitskopie direkt unter `base_dirs` wird überwacht (`depth: 1`; erhöhen Sie den Wert, wenn Ihre Repositories in Unterverzeichnissen liegen). Geheimnisse bleiben außerhalb der Datei: sie werden aus der Umgebung gelesen, und [`config.example.yaml`](../../config.example.yaml) dokumentiert alle anderen Schlüssel.

### Schritt 4 — Den Dienst starten

**Mit Docker** (empfohlen). Weiterhin in `deploy/`: die Geheimnisse in eine `.env` neben der `docker-compose.yml` legen und uid/gid des Besitzers der Repositories ermitteln:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Bearbeiten Sie dann [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml): diese uid/gid in `user:`, und Ihre `base_dirs` **unter demselben Pfad innen und außen** eingebunden (die Karte zeigt dann den echten Pfad des Hosts). Der Start mit einer anderen uid ist der klassische Fehler: `git fetch` würde Dateien hinterlassen, die Ihr eigenes Konto nicht mehr ändern kann.

```bash
docker compose up -d
docker compose logs -f
```

**Ohne Docker** (Python ≥ 3.12), mit der systemd-Benutzer-Unit [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # ohne offene Sitzung weiterlaufen lassen
journalctl --user -u git-repo-monitor -f
```

In beiden Fällen sollte das Log pro Repository eine Zeile `Repository <Name> found at <Pfad>` zeigen, dann `Watching N git directories`. Eine Zeile `Configuration error: …` heißt, dass `config.yaml` abgelehnt wurde; die Tabelle am Ende dieses Abschnitts nennt die üblichen Ursachen.

### Schritt 5 — In Home Assistant prüfen

*Einstellungen → Geräte & Dienste → MQTT → Geräte*: ein Gerät pro Repository, plus eines für den Dienst selbst. Öffnen Sie eines davon — `sensor.git_<repo>_status` sollte schon `ok`, `needs_sync`, `ci_failed`… enthalten. Erscheint dort nichts, obwohl das Log sauber ist, liest Home Assistant nicht den Broker, in den der Dienst schreibt.

### Schritt 6 — Die Karte installieren

*HACS → drei Punkte (oben rechts) → Benutzerdefinierte Repositories*: URL `https://github.com/Elwinmage/ha-git-repo-monitor`, Typ **Dashboard**, *Hinzufügen*. Öffnen Sie dann *Git Repo Monitor card* und laden Sie sie herunter. Laden Sie die Seite neu (`Strg-Shift-R`), damit der Browser die neue Datei übernimmt.

### Schritt 7 — Die Karte einem Dashboard hinzufügen

*Dashboard bearbeiten → Karte hinzufügen*, dann nach *Git Repo Monitor* suchen. Sie findet die Repositories von selbst; alles andere ist optional und lässt sich im visuellen Editor einstellen, siehe [Kartenoptionen](#kartenoptionen).

Die Installation ist fertig. Der nächste Schritt ist optional: er lässt den GitHub-Teil der Karte in Sekunden statt in Minuten reagieren.

### Schritt 8 (optional) — Echtzeit-Webhooks

Ohne Webhooks wird ein Push von einer anderen Maschine oder das Ende eines geplanten Runs spätestens nach 20 Minuten gesehen. Mit Webhooks innerhalb von Sekunden.

1. In `config.yaml` `webhook.enabled: true` setzen, das Geheimnis in `GRM_WEBHOOK_SECRET` eintragen (in `.env` oder in der Datei `env`) und den Dienst neu starten: das Log zeigt `Webhook listening on 0.0.0.0:8099/webhook`.
2. Geben Sie diesen Port über Ihren Reverse Proxy per HTTPS frei. Auf einer Synology: *Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy*, Quelle `https://ihre.domain:443` mit eigenem Hostnamen oder Port, Ziel `http://<Maschine>:8099`. Nur `POST /webhook` und `GET /health` antworten, und jede Anfrage wird über ihre HMAC-Signatur geprüft — `curl https://ihre.domain/health` sollte `ok` antworten.
3. Erstellen Sie die Webhooks mit der GitHub CLI (`gh auth login`, mit dem Scope `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://ihre.domain/webhook            # alle Ihre Repositories
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://ihre.domain/webhook owner/repo # einige davon
```

Das Skript kann erneut laufen: es aktualisiert die vorhandenen Webhooks. Starten Sie es neu, wenn Sie ein Repository anlegen. Auf GitHub zeigt *Settings → Webhooks* eines Repositories jede Zustellung und ihre Antwort (`202 accepted`).

### Wenn etwas nicht klappt

| Symptom | Wo nachsehen |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | falsche Datei gelesen (`-c`) oder `base_dirs` leer gelassen |
| `MQTT connection lost: …` in Dauerschleife | Broker-Adresse, Benutzername, `GRM_MQTT_PASSWORD` |
| Kein Gerät in Home Assistant, obwohl das Log sauber ist | Integration **MQTT** fehlt oder `discovery_prefix` ungleich `homeassistant` |
| `Cannot list <Verzeichnis>` | falsche `base_dirs` oder für die uid des Dienstes unlesbares Verzeichnis |
| Kein Repository gefunden | Arbeitskopien tiefer als `depth` oder zu weites `exclude`-Muster |
| Repositories veröffentlicht, aber keine GitHub-Daten | kein Token (`No GitHub token` im Log) oder fehlende Berechtigungen |
| `git fetch failed for <repo>` | Token ohne **Contents** (lesen) oder privates Repository außerhalb seines Geltungsbereichs |
| `dubious ownership` im Log | Container mit einer anderen uid gestartet als der Besitzer der Repositories |
| *Git Repo Monitor* fehlt in der Kartenliste | HACS-Download nicht beendet oder Seite nicht neu geladen |

## Home-Assistant-Entitäten

Ein Gerät pro Repository (`<host>` ist `host_id`, `<repo>` der Verzeichnisname):

| Entität | Inhalt |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; alle Details als Attribute |
| `binary_sensor.git_<repo>_ci_problem` | ein Workflow ist fehlgeschlagen (für Ihre Benachrichtigungen) |
| `binary_sensor.git_<repo>_needs_sync` | nicht committete Änderungen oder Commits zum Pushen/Pullen |
| `sensor.git_<repo>_version` | Version in der Arbeitskopie |
| `sensor.git_<repo>_stars` | Sterne (Langzeitstatistik) |
| `sensor.git_<repo>_active_installs` | aktive Installationen (nur Integrationen) |
| `sensor.git_<repo>_coverage` | Abdeckung in % (falls ein Bericht existiert) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | Befehle |

Dazu ein Gerät für den Dienst: Anzahl der Repositories, verbleibendes GitHub-API-Kontingent, Schaltfläche *Refresh all*. Alle Entitäten werden nicht verfügbar, wenn der Dienst stoppt (MQTT Last Will).

## Kartenoptionen

```yaml
type: custom:git-repo-monitor-card
title: Meine Repositories  # optional
host: alare                # nur dieser Rechner (Standard: alle)
sort: status               # status (Probleme zuerst) | name
show_ok: true              # aktuelle Repositories anzeigen
expand_problems: false     # Repositories mit CI-Fehler aufklappen
compact: false             # eine Zeile pro Repository
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Alles lässt sich im visuellen Editor einstellen. Ein Klick auf eine Zeile zeigt die fehlgeschlagenen Workflows, den letzten Commit, das letzte Release, die Installationen pro Version und die Schaltflächen *Aktualisieren*, *Fetch* und *Fehlgeschlagene neu starten*.

## MQTT-Topics

| Topic | Inhalt |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retained, Last Will) |
| `git_repo_monitor/<host>/<repo>/state` | JSON-Zustand des Repositorys (retained) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | Zusammenfassung des Dienstes |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Entwicklung

```bash
pip install -e ".[test]" && pytest     # 100 % Abdeckung erforderlich, Bericht in reports/
cd card && npm install && npm run build
```

Releases entstehen mit dem Workflow *Release* (Versionsnummer als Eingabe): Er aktualisiert die Versionen, baut die Karte, die dem GitHub-Release beigefügt wird, und veröffentlicht das Image `ghcr.io/elwinmage/ha-git-repo-monitor`.

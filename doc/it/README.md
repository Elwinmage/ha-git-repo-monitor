# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | [🇫🇷 Français](../fr/README.md) | [🇩🇪 Deutsch](../de/README.md) | [🇪🇸 Español](../es/README.md) | 🇮🇹 Italiano | [🇵🇱 Polski](../pl/README.md) | [🇵🇹 Português](../pt/README.md)

Una panoramica di tutti i vostri repository git in Home Assistant: stato locale, GitHub Actions, release, stelle e installazioni attive. Viene aggiornata **solo quando qualcosa cambia**: un commit, un push o la fine di un workflow.

![Scheda](../img/card.png)

Il progetto ha due parti in un unico repository:

- **un servizio** (Python) che gira sulla macchina dove si trovano le vostre copie di lavoro. Pubblica tutto via MQTT con il rilevamento automatico di Home Assistant;
- **una scheda Lovelace** (`custom:git-repo-monitor-card`), installata con HACS.

## Funzionamento

| Evento | Cosa viene aggiornato |
|---|---|
| Modifica in `.git` (inotify): commit, checkout, merge, fetch… | stato locale del repository, circa 2 s dopo |
| Ogni `status_interval` (90 s) | modifiche non committate, rapporto di copertura |
| Webhook GitHub (opzionale) | stato GitHub del repository, poi modalità «calda» |
| Il vostro flusso di attività GitHub, ogni 60 s (richieste condizionali gratuite) | repository su cui avete fatto push o pubblicato una release |
| Modalità «calda», ogni 20 s | dopo un push: fetch + workflow, finché tutte le esecuzioni non sono terminate |
| Ogni 20 min | aggiornamento completo (rete di sicurezza) |
| Una volta al giorno | installazioni attive (analytics.home-assistant.io), riferimento giornaliero delle stelle |

Dati locali: ramo, ramo remoto, commit da inviare o scaricare, modifiche non committate, ultimo tag, ultimo commit, versione dal `manifest.json` o dal `package.json`, copertura letta in `reports/coverage.xml` (formato Cobertura: il servizio non esegue mai i test).

Dati GitHub: ultima esecuzione di ogni workflow, ultima release e commit successivi, pull request aperte (quelle di Dependabot contate a parte), issue, stelle e fork.

Installazioni attive: prese dal file pubblico delle statistiche di Home Assistant, per i repository che contengono un'integrazione (`custom_components/<dominio>/manifest.json`). Contano solo gli utenti che hanno attivato le statistiche di utilizzo; schede e blueprint non hanno un numero.

## Installazione

### 1. Il servizio

Copiate [`config.example.yaml`](../../config.example.yaml) in `config.yaml` e adattatelo (broker MQTT, cartelle dei repository).

**Docker** (consigliato) — vedere [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml):

```bash
cd deploy
cp ../config.example.yaml config.yaml   # poi modificatelo
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Montate i repository allo stesso percorso dell'host. Avviate il container con l'uid proprietario dei repository (`user:`), altrimenti `git fetch` lascia file che il vostro account non può modificare.

**Senza Docker** (Python ≥ 3.12): `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, poi l'unità systemd utente [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service).

### 2. La scheda

HACS → tre puntini → *Repository personalizzati* → `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Dashboard**. Aggiungete poi la scheda *Git Repo Monitor*: trova i repository da sola.

## Token GitHub

Create un token *fine-grained* sui vostri repository con i permessi **Metadata** (lettura), **Contents** (lettura), **Actions** (lettura e scrittura: la scrittura serve solo per *Rilancia i fallimenti*) e **Pull requests** (lettura). Passatelo con `GITHUB_TOKEN`.

Lo stesso token serve per `git fetch`: i remote SSH (`git@github.com:…`) vengono riscritti in HTTPS per quel comando, quindi nel container non serve alcuna chiave SSH. Senza token viene pubblicato solo lo stato locale.

## Webhook in tempo reale (opzionale)

Senza webhook, un push da un'altra macchina o la fine di un'esecuzione pianificata viene visto al massimo entro 20 minuti. Con i webhook, in pochi secondi.

1. Attivate `webhook` in `config.yaml` con un segreto (o `GRM_WEBHOOK_SECRET`).
2. Esponete la porta del servizio tramite il vostro reverse proxy, in HTTPS. Su un Synology: *Pannello di controllo → Portale di accesso → Avanzate → Proxy inverso*, origine `https://vostro.dominio:443` con un nome host o una porta dedicati, destinazione `http://<macchina>:8099`. Rispondono solo `POST /webhook` e `GET /health`, e ogni richiesta viene verificata con la sua firma HMAC.
3. Create i webhook (GitHub CLI con lo scope `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://vostro.dominio/webhook            # tutti i vostri repository
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://vostro.dominio/webhook owner/repo # alcuni
```

Lo script può essere rieseguito: aggiorna i webhook esistenti. Rieseguitelo quando create un repository.

## Entità di Home Assistant

Un dispositivo per repository (`<host>` è `host_id`, `<repo>` il nome della cartella):

| Entità | Contenuto |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; tutti i dettagli come attributi |
| `binary_sensor.git_<repo>_ci_problem` | un workflow è fallito (per le vostre notifiche) |
| `binary_sensor.git_<repo>_needs_sync` | modifiche non committate o commit da inviare/scaricare |
| `sensor.git_<repo>_version` | versione nella copia di lavoro |
| `sensor.git_<repo>_stars` | stelle (statistiche a lungo termine) |
| `sensor.git_<repo>_active_installs` | installazioni attive (solo integrazioni) |
| `sensor.git_<repo>_coverage` | copertura in % (se esiste un rapporto) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | comandi |

In più un dispositivo per il servizio: numero di repository, quota residua dell'API GitHub, pulsante *Refresh all*. Tutte le entità diventano non disponibili quando il servizio si ferma (ultime volontà MQTT).

## Opzioni della scheda

```yaml
type: custom:git-repo-monitor-card
title: I miei repository   # opzionale
host: alare                # solo questa macchina (predefinito: tutte)
sort: status               # status (problemi prima) | name
show_ok: true              # mostra i repository aggiornati
expand_problems: false     # espandi i repository con CI fallita
compact: false             # una riga per repository
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Tutto si regola nell'editor visuale. Un clic su una riga mostra i workflow falliti, l'ultimo commit, l'ultima release, le installazioni per versione e i pulsanti *Aggiorna*, *Fetch* e *Rilancia i fallimenti*.

## Topic MQTT

| Topic | Contenuto |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retained, ultime volontà) |
| `git_repo_monitor/<host>/<repo>/state` | stato JSON del repository (retained) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | riepilogo del servizio |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Sviluppo

```bash
pip install -e ".[test]" && pytest     # copertura del 100 % richiesta, rapporto in reports/
cd card && npm install && npm run build
```

Le versioni si pubblicano con il workflow *Release* (numero di versione in ingresso): aggiorna le versioni, compila la scheda allegata alla release GitHub e pubblica l'immagine `ghcr.io/elwinmage/ha-git-repo-monitor`.

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

Questo tutorial parte da una macchina vuota e arriva a una scheda funzionante. Conti una ventina di minuti.

Prima di cominciare servono: la macchina su cui stanno le sue copie di lavoro, con **Docker** o **Python ≥ 3.12**; un **broker MQTT** già collegato a Home Assistant (il componente aggiuntivo Mosquitto va benissimo) e l'integrazione **MQTT** configurata; **HACS** installato. Il token GitHub è opzionale: senza di esso la scheda mostra comunque lo stato locale.

### Passo 1 — Ottenere un accesso MQTT

Il servizio si collega al broker come qualsiasi altro client. Con il componente aggiuntivo Mosquitto, la via più semplice è un utente Home Assistant dedicato: *Impostazioni → Persone → Aggiungi persona*, attivi *Consenti alla persona di accedere*, lo chiami `git_monitor`. Annoti l'indirizzo del broker (la macchina Home Assistant, porta 1883 per impostazione predefinita) e quella coppia utente/password.

### Passo 2 — Creare il token GitHub (consigliato)

Su GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories**, o solo quelli che sorveglia;
- *Permissions*: **Metadata** (lettura), **Contents** (lettura), **Actions** (lettura **e scrittura** — la scrittura serve solo a *Rilanciare i fallimenti*), **Pull requests** (lettura).

Copi subito il valore `github_pat_…`, GitHub non lo mostrerà più. Lo stesso token serve a `git fetch`: i remoti SSH (`git@github.com:…`) vengono riscritti in HTTPS per quel comando, quindi non serve alcuna chiave SSH nel container. Senza token il servizio scrive `No GitHub token: only the local state is monitored` nei log e pubblica solo lo stato locale.

### Passo 3 — Scrivere la configurazione

Sulla macchina dove stanno i repository:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Tre chiavi da adattare in `config.yaml`, il resto può restare così:

```yaml
host_id: alare                       # nome di questa macchina in HA (predefinito: il nome host)
mqtt:
  host: 192.168.0.109                # il suo broker (passo 1)
  username: git_monitor              # la password arriva dall'ambiente, passo 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # directory che contiene le sue copie di lavoro
```

Viene sorvegliata ogni copia di lavoro git direttamente sotto `base_dirs` (`depth: 1`; lo aumenti se i suoi repository stanno in sottodirectory). I segreti restano fuori dal file: vengono letti dall'ambiente, e [`config.example.yaml`](../../config.example.yaml) documenta tutte le altre chiavi.

### Passo 4 — Avviare il servizio

**Con Docker** (consigliato). Sempre in `deploy/`, metta i segreti in un file `.env` accanto al `docker-compose.yml` e rilevi uid/gid proprietario dei repository:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Modifichi poi [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml): quegli uid/gid in `user:`, e le sue `base_dirs` montate **allo stesso percorso dentro e fuori** (la scheda mostra così il vero percorso dell'host). Avviare il container con un altro uid è l'errore classico: `git fetch` lascerebbe file che il suo stesso account non può più modificare.

```bash
docker compose up -d
docker compose logs -f
```

**Senza Docker** (Python ≥ 3.12), con l'unità systemd utente [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # tenerlo attivo senza sessione aperta
journalctl --user -u git-repo-monitor -f
```

In entrambi i casi i log devono mostrare una riga `Repository <nome> found at <percorso>` per repository, poi `Watching N git directories`. Una riga `Configuration error: …` significa che `config.yaml` è stato rifiutato: la tabella a fine sezione elenca le cause abituali.

### Passo 5 — Verificare in Home Assistant

*Impostazioni → Dispositivi e servizi → MQTT → dispositivi*: un dispositivo per repository, più uno per il servizio stesso. Ne apra uno — `sensor.git_<repo>_status` deve già contenere `ok`, `needs_sync`, `ci_failed`… Se non appare nulla mentre i log sono puliti, Home Assistant non legge il broker su cui scrive il servizio.

### Passo 6 — Installare la scheda

*HACS → tre punti (in alto a destra) → Repository personalizzati*: URL `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Dashboard**, *Aggiungi*. Apra poi *Git Repo Monitor card* e la scarichi. Ricarichi la pagina (`Ctrl-Maiusc-R`) perché il browser prenda il nuovo file.

### Passo 7 — Aggiungere la scheda a una dashboard

*Modifica dashboard → Aggiungi scheda*, poi cerchi *Git Repo Monitor*. Trova i repository da sola; tutto il resto è opzionale e si regola nell'editor visuale, veda [Opzioni della scheda](#opzioni-della-scheda).

L'installazione è finita. Il passo seguente è opzionale: fa reagire la parte GitHub della scheda in qualche secondo invece di qualche minuto.

### Passo 8 (opzionale) — Webhook in tempo reale

Senza webhook, un push da un'altra macchina o la fine di un run pianificato si vede al massimo in 20 minuti. Con i webhook, in qualche secondo.

1. In `config.yaml` metta `webhook.enabled: true`, aggiunga il segreto in `GRM_WEBHOOK_SECRET` (in `.env` o nel file `env`) e riavvii il servizio: il log mostra `Webhook listening on 0.0.0.0:8099/webhook`.
2. Esponga quella porta tramite il suo reverse proxy, in HTTPS. Su un Synology: *Pannello di controllo → Portale di accesso → Avanzate → Proxy inverso*, origine `https://suo.dominio:443` con un nome host o una porta dedicati, destinazione `http://<macchina>:8099`. Rispondono solo `POST /webhook` e `GET /health`, e ogni richiesta è verificata con la sua firma HMAC — `curl https://suo.dominio/health` deve rispondere `ok`.
3. Crei i webhook con GitHub CLI (`gh auth login`, con lo scope `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://suo.dominio/webhook            # tutti i suoi repository
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://suo.dominio/webhook owner/repo # alcuni
```

Lo script può essere rilanciato: aggiorna i webhook esistenti. Lo rilanci quando crea un repository. Su GitHub, *Settings → Webhooks* di un repository mostra ogni consegna e la sua risposta (`202 accepted`).

### In caso di problemi

| Sintomo | Dove guardare |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | file sbagliato (`-c`) o `base_dirs` lasciato vuoto |
| `MQTT connection lost: …` in ciclo | indirizzo del broker, utente, `GRM_MQTT_PASSWORD` |
| Nessun dispositivo in Home Assistant pur con log puliti | integrazione **MQTT** assente, o `discovery_prefix` diverso da `homeassistant` |
| `Cannot list <directory>` | `base_dirs` errato, o directory illeggibile per l'uid del servizio |
| Nessun repository trovato | copie di lavoro più profonde di `depth`, o modello `exclude` troppo ampio |
| Repository pubblicati ma nessun dato GitHub | nessun token (`No GitHub token` nei log) o permessi mancanti |
| `git fetch failed for <repo>` | token senza **Contents** (lettura), o repository privato fuori dal suo ambito |
| `dubious ownership` nei log | container avviato con un uid diverso dal proprietario dei repository |
| *Git Repo Monitor* assente dalla lista delle schede | download HACS non terminato, o pagina non ricaricata |

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
repos:                     # solo questi repository, in quest'ordine (predefinito: tutti)
  - alare/ha-reef-card
  - alare/ha-reefbeat-component
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

Tutto si regola nell'editor visuale, compresa la lista dei repository: aggiunga quelli che vuole, poi li sposti su o giù fino a ottenere l'ordine desiderato. Lista vuota: tutti i repository, ordinati secondo `sort`; una lista compilata prevale su `sort`. Una voce può anche essere un semplice nome di directory (`ha-reef-card`), per riconoscerlo su qualsiasi macchina. Un clic su una riga mostra i workflow falliti, l'ultimo commit, l'ultima release, le installazioni per versione e i pulsanti *Aggiorna*, *Fetch* e *Rilancia i fallimenti*.

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

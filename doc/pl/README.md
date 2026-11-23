# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | [🇫🇷 Français](../fr/README.md) | [🇩🇪 Deutsch](../de/README.md) | [🇪🇸 Español](../es/README.md) | [🇮🇹 Italiano](../it/README.md) | 🇵🇱 Polski | [🇵🇹 Português](../pt/README.md)

Przegląd wszystkich Twoich repozytoriów git w Home Assistant: stan lokalny, GitHub Actions, wydania, gwiazdki i aktywne instalacje. Odświeża się **tylko wtedy, gdy coś się zmienia**: commit, push albo zakończenie workflowu.

![Karta](../img/card.png)

Projekt składa się z dwóch części w jednym repozytorium:

- **usługa** (Python) działająca na maszynie z kopiami roboczymi. Publikuje wszystko przez MQTT z automatycznym wykrywaniem Home Assistant;
- **karta Lovelace** (`custom:git-repo-monitor-card`), instalowana przez HACS.

## Jak to działa

| Wyzwalacz | Co jest odświeżane |
|---|---|
| Zmiana w `.git` (inotify): commit, checkout, merge, fetch… | stan lokalny repozytorium, około 2 s później |
| Co `status_interval` (90 s) | niezatwierdzone zmiany, raport pokrycia |
| Webhook GitHub (opcjonalnie) | stan repozytorium na GitHubie, potem tryb „gorący" |
| Twój kanał aktywności GitHub, co 60 s (bezpłatne zapytania warunkowe) | repozytoria, do których wypchnięto zmiany lub opublikowano wydanie |
| Tryb „gorący", co 20 s | po pushu: fetch + workflowy, aż wszystkie uruchomienia się zakończą |
| Co 20 min | pełne odświeżenie (zabezpieczenie) |
| Raz dziennie | aktywne instalacje (analytics.home-assistant.io), dzienny punkt odniesienia gwiazdek |

Dane lokalne: gałąź, gałąź zdalna, commity do wypchnięcia lub pobrania, niezatwierdzone zmiany, ostatni tag, ostatni commit, wersja z `manifest.json` lub `package.json`, pokrycie odczytane z `reports/coverage.xml` (format Cobertura: usługa nigdy nie uruchamia testów).

Dane z GitHuba: ostatnie uruchomienie każdego workflowu, ostatnie wydanie i commity od tego czasu, otwarte pull requesty (te od Dependabota liczone osobno), zgłoszenia, gwiazdki i forki.

Aktywne instalacje: pobierane z publicznego pliku statystyk Home Assistant, dla repozytoriów zawierających integrację (`custom_components/<domena>/manifest.json`). Liczeni są tylko użytkownicy, którzy włączyli statystyki użycia; karty i blueprinty nie mają liczby.

## Instalacja

### 1. Usługa

Skopiuj [`config.example.yaml`](../../config.example.yaml) do `config.yaml` i dostosuj go (broker MQTT, katalogi z repozytoriami).

**Docker** (zalecany) — zobacz [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml):

```bash
cd deploy
cp ../config.example.yaml config.yaml   # następnie go edytuj
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Zamontuj repozytoria pod tą samą ścieżką co na hoście. Uruchom kontener z uid właściciela repozytoriów (`user:`), w przeciwnym razie `git fetch` zostawi pliki, których Twoje konto nie może modyfikować.

**Bez Dockera** (Python ≥ 3.12): `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, a następnie jednostka użytkownika systemd [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service).

### 2. Karta

HACS → trzy kropki → *Niestandardowe repozytoria* → `https://github.com/Elwinmage/ha-git-repo-monitor`, typ **Dashboard**. Następnie dodaj kartę *Git Repo Monitor*: sama znajduje repozytoria.

## Token GitHub

Utwórz token *fine-grained* dla swoich repozytoriów z uprawnieniami **Metadata** (odczyt), **Contents** (odczyt), **Actions** (odczyt i zapis: zapis jest potrzebny tylko do *Uruchom ponownie nieudane*) oraz **Pull requests** (odczyt). Przekaż go przez `GITHUB_TOKEN`.

Ten sam token służy do `git fetch`: zdalne repozytoria SSH (`git@github.com:…`) są na potrzeby tego polecenia przepisywane na HTTPS, więc w kontenerze nie jest potrzebny klucz SSH. Bez tokenu publikowany jest tylko stan lokalny.

## Webhooki w czasie rzeczywistym (opcjonalnie)

Bez webhooków push z innej maszyny lub zakończenie zaplanowanego uruchomienia jest widoczne najpóźniej po 20 minutach. Z webhookami — w kilka sekund.

1. Włącz `webhook` w `config.yaml` z sekretem (lub `GRM_WEBHOOK_SECRET`).
2. Udostępnij port usługi przez swój reverse proxy, po HTTPS. Na Synology: *Panel sterowania → Portal logowania → Zaawansowane → Odwrotny serwer proxy*, źródło `https://twoja.domena:443` z osobną nazwą hosta lub portem, cel `http://<maszyna>:8099`. Odpowiadają tylko `POST /webhook` i `GET /health`, a każde żądanie jest sprawdzane podpisem HMAC.
3. Utwórz webhooki (GitHub CLI z zakresem `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://twoja.domena/webhook            # wszystkie repozytoria
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://twoja.domena/webhook owner/repo # wybrane
```

Skrypt można uruchamiać wielokrotnie: aktualizuje istniejące webhooki. Uruchom go ponownie po utworzeniu repozytorium.

## Encje Home Assistant

Jedno urządzenie na repozytorium (`<host>` to `host_id`, `<repo>` nazwa katalogu):

| Encja | Zawartość |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; wszystkie szczegóły jako atrybuty |
| `binary_sensor.git_<repo>_ci_problem` | workflow zakończył się błędem (do powiadomień) |
| `binary_sensor.git_<repo>_needs_sync` | niezatwierdzone zmiany lub commity do wypchnięcia/pobrania |
| `sensor.git_<repo>_version` | wersja w kopii roboczej |
| `sensor.git_<repo>_stars` | gwiazdki (statystyki długoterminowe) |
| `sensor.git_<repo>_active_installs` | aktywne instalacje (tylko integracje) |
| `sensor.git_<repo>_coverage` | pokrycie w % (jeśli istnieje raport) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | polecenia |

Do tego jedno urządzenie usługi: liczba repozytoriów, pozostały limit API GitHuba, przycisk *Refresh all*. Wszystkie encje stają się niedostępne, gdy usługa się zatrzyma (MQTT last will).

## Opcje karty

```yaml
type: custom:git-repo-monitor-card
title: Moje repozytoria    # opcjonalnie
host: alare                # tylko ta maszyna (domyślnie: wszystkie)
sort: status               # status (najpierw problemy) | name
show_ok: true              # pokazuj aktualne repozytoria
expand_problems: false     # rozwijaj repozytoria z nieudanym CI
compact: false             # jeden wiersz na repozytorium
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Wszystko można ustawić w edytorze wizualnym. Kliknięcie wiersza pokazuje nieudane workflowy, ostatni commit, ostatnie wydanie, instalacje według wersji oraz przyciski *Odśwież*, *Fetch* i *Uruchom ponownie nieudane*.

## Tematy MQTT

| Temat | Zawartość |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retained, last will) |
| `git_repo_monitor/<host>/<repo>/state` | stan repozytorium w JSON (retained) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | podsumowanie usługi |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Rozwój

```bash
pip install -e ".[test]" && pytest     # wymagane 100 % pokrycia, raport w reports/
cd card && npm install && npm run build
```

Wydania tworzy workflow *Release* (numer wersji jako parametr): aktualizuje wersje, buduje kartę dołączaną do wydania na GitHubie i publikuje obraz `ghcr.io/elwinmage/ha-git-repo-monitor`.

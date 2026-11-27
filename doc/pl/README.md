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

Ten przewodnik prowadzi od pustej maszyny do działającej karty. Licz na jakieś dwadzieścia minut.

Zanim zaczniesz, potrzebujesz: maszyny, na której są twoje kopie robocze, z **Dockerem** albo **Pythonem ≥ 3.12**; **brokera MQTT** już połączonego z Home Assistant (dodatek Mosquitto nadaje się doskonale) i skonfigurowanej integracji **MQTT**; zainstalowanego **HACS**. Token GitHuba jest opcjonalny: bez niego karta nadal pokazuje stan lokalny.

### Krok 1 — Zdobądź dostęp do MQTT

Usługa loguje się do brokera jak każdy inny klient. Przy dodatku Mosquitto najprościej jest utworzyć osobnego użytkownika Home Assistant: *Ustawienia → Osoby → Dodaj osobę*, włącz *Zezwól na logowanie*, nazwij go `git_monitor`. Zapisz adres brokera (maszyna z Home Assistant, domyślnie port 1883) oraz tę nazwę i hasło.

### Krok 2 — Utwórz token GitHuba (zalecane)

Na GitHubie: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories** albo tylko te, które monitorujesz;
- *Permissions*: **Metadata** (odczyt), **Contents** (odczyt), **Actions** (odczyt **i zapis** — zapis służy tylko do *Ponownego uruchomienia nieudanych*), **Pull requests** (odczyt).

Skopiuj od razu wartość `github_pat_…`, GitHub nigdy jej ponownie nie pokaże. Ten sam token służy do `git fetch`: zdalne adresy SSH (`git@github.com:…`) są dla tej komendy przepisywane na HTTPS, więc klucz SSH w kontenerze nie jest potrzebny. Bez tokena usługa wypisuje w logach `No GitHub token: only the local state is monitored` i publikuje tylko stan lokalny.

### Krok 3 — Zapisz konfigurację

Na maszynie, na której są repozytoria:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Trzy klucze do dostosowania w `config.yaml`, reszta może zostać bez zmian:

```yaml
host_id: alare                       # nazwa tej maszyny w HA (domyślnie: nazwa hosta)
mqtt:
  host: 192.168.0.109                # twój broker (krok 1)
  username: git_monitor              # hasło pochodzi ze środowiska, krok 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # katalog z twoimi kopiami roboczymi
```

Monitorowana jest każda kopia robocza git leżąca bezpośrednio pod `base_dirs` (`depth: 1`; zwiększ tę wartość, jeśli twoje repozytoria są w podkatalogach). Sekrety zostają poza plikiem: są czytane ze środowiska, a [`config.example.yaml`](../../config.example.yaml) opisuje wszystkie pozostałe klucze.

### Krok 4 — Uruchom usługę

**Z Dockerem** (zalecane). Nadal w `deploy/` umieść sekrety w pliku `.env` obok `docker-compose.yml` i sprawdź uid/gid właściciela repozytoriów:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Następnie zmodyfikuj [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml): te uid/gid w `user:` oraz twoje `base_dirs` zamontowane **pod tą samą ścieżką wewnątrz i na zewnątrz** (karta pokaże wtedy prawdziwą ścieżkę hosta). Uruchomienie kontenera z innym uid to klasyczny błąd: `git fetch` zostawiłby pliki, których twoje własne konto nie może już zmienić.

```bash
docker compose up -d
docker compose logs -f
```

**Bez Dockera** (Python ≥ 3.12), z jednostką systemd użytkownika [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # działanie bez otwartej sesji
journalctl --user -u git-repo-monitor -f
```

W obu przypadkach logi powinny pokazać po jednej linii `Repository <nazwa> found at <ścieżka>` na repozytorium, a potem `Watching N git directories`. Linia `Configuration error: …` oznacza, że `config.yaml` został odrzucony: tabela na końcu tej sekcji podaje typowe przyczyny.

### Krok 5 — Sprawdź w Home Assistant

*Ustawienia → Urządzenia i usługi → MQTT → urządzenia*: jedno urządzenie na repozytorium plus jedno dla samej usługi. Otwórz któreś — `sensor.git_<repo>_status` powinien już zawierać `ok`, `needs_sync`, `ci_failed`… Jeśli nic się nie pojawia, choć logi są czyste, Home Assistant nie czyta brokera, do którego pisze usługa.

### Krok 6 — Zainstaluj kartę

*HACS → trzy kropki (prawy górny róg) → Własne repozytoria*: adres `https://github.com/Elwinmage/ha-git-repo-monitor`, typ **Dashboard**, *Dodaj*. Otwórz potem *Git Repo Monitor card* i pobierz ją. Odśwież stronę (`Ctrl-Shift-R`), aby przeglądarka wzięła nowy plik.

### Krok 7 — Dodaj kartę do dashboardu

*Edytuj dashboard → Dodaj kartę*, potem poszukaj *Git Repo Monitor*. Repozytoria znajduje sama; wszystko inne jest opcjonalne i ustawia się w edytorze wizualnym, zobacz [Opcje karty](#opcje-karty).

Instalacja jest zakończona. Następny krok jest opcjonalny: sprawia, że część GitHubowa karty reaguje w sekundy, a nie w minuty.

### Krok 8 (opcjonalnie) — Webhooki w czasie rzeczywistym

Bez webhooków push z innej maszyny albo koniec zaplanowanego uruchomienia widać najpóźniej po 20 minutach. Z webhookami — w ciągu kilku sekund.

1. W `config.yaml` ustaw `webhook.enabled: true`, dodaj sekret w `GRM_WEBHOOK_SECRET` (w `.env` albo w pliku `env`) i zrestartuj usługę: log pokaże `Webhook listening on 0.0.0.0:8099/webhook`.
2. Udostępnij ten port przez swój reverse proxy, po HTTPS. Na Synology: *Panel sterowania → Portal logowania → Zaawansowane → Reverse Proxy*, źródło `https://twoja.domena:443` z osobną nazwą hosta lub portem, cel `http://<maszyna>:8099`. Odpowiadają tylko `POST /webhook` i `GET /health`, a każde żądanie jest sprawdzane podpisem HMAC — `curl https://twoja.domena/health` powinno odpowiedzieć `ok`.
3. Utwórz webhooki za pomocą GitHub CLI (`gh auth login`, z zakresem `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://twoja.domena/webhook            # wszystkie twoje repozytoria
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://twoja.domena/webhook owner/repo # wybrane
```

Skrypt można uruchomić ponownie: aktualizuje istniejące webhooki. Uruchom go znowu, gdy utworzysz repozytorium. Na GitHubie *Settings → Webhooks* repozytorium pokazuje każdą dostawę i jej odpowiedź (`202 accepted`).

### Jeśli coś nie działa

| Objaw | Gdzie szukać |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | zły plik (`-c`) albo puste `base_dirs` |
| `MQTT connection lost: …` w pętli | adres brokera, nazwa użytkownika, `GRM_MQTT_PASSWORD` |
| Brak urządzeń w Home Assistant przy czystych logach | brak integracji **MQTT** albo `discovery_prefix` inny niż `homeassistant` |
| `Cannot list <katalog>` | złe `base_dirs` albo katalog nieczytelny dla uid usługi |
| Nie znaleziono żadnego repozytorium | kopie robocze głębiej niż `depth` albo zbyt szeroki wzorzec `exclude` |
| Repozytoria opublikowane, ale bez danych z GitHuba | brak tokena (`No GitHub token` w logach) albo brakujące uprawnienia |
| `git fetch failed for <repo>` | token bez **Contents** (odczyt) albo prywatne repozytorium poza jego zakresem |
| `dubious ownership` w logach | kontener uruchomiony z innym uid niż właściciel repozytoriów |
| Brak *Git Repo Monitor* na liście kart | niezakończone pobieranie w HACS albo nieodświeżona strona |

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

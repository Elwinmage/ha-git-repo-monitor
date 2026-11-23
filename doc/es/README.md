# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | [🇫🇷 Français](../fr/README.md) | [🇩🇪 Deutsch](../de/README.md) | 🇪🇸 Español | [🇮🇹 Italiano](../it/README.md) | [🇵🇱 Polski](../pl/README.md) | [🇵🇹 Português](../pt/README.md)

Una vista de conjunto de todos sus repositorios git en Home Assistant: estado local, GitHub Actions, releases, estrellas e instalaciones activas. Se actualiza **solo cuando algo cambia**: un commit, un push o el final de un workflow.

![Tarjeta](../img/card.png)

El proyecto tiene dos partes en un mismo repositorio:

- **un servicio** (Python) que se ejecuta en la máquina donde están sus copias de trabajo. Lo publica todo por MQTT con el descubrimiento automático de Home Assistant;
- **una tarjeta Lovelace** (`custom:git-repo-monitor-card`), instalada con HACS.

## Funcionamiento

| Disparador | Qué se actualiza |
|---|---|
| Cambio en `.git` (inotify): commit, checkout, merge, fetch… | estado local del repositorio, unos 2 s después |
| Cada `status_interval` (90 s) | cambios sin confirmar, informe de cobertura |
| Webhook de GitHub (opcional) | estado GitHub del repositorio, luego modo «caliente» |
| Su flujo de actividad de GitHub, cada 60 s (peticiones condicionales gratuitas) | repositorios en los que ha hecho push o publicado una release |
| Modo «caliente», cada 20 s | tras un push: fetch + workflows, hasta que terminen todas las ejecuciones |
| Cada 20 min | actualización completa (red de seguridad) |
| Una vez al día | instalaciones activas (analytics.home-assistant.io), referencia diaria de estrellas |

Datos locales: rama, rama remota, commits por subir o bajar, cambios sin confirmar, último tag, último commit, versión del `manifest.json` o del `package.json`, cobertura leída en `reports/coverage.xml` (formato Cobertura: el servicio nunca ejecuta las pruebas).

Datos de GitHub: última ejecución de cada workflow, última release y commits desde entonces, pull requests abiertas (las de Dependabot contadas aparte), issues, estrellas y forks.

Instalaciones activas: tomadas del fichero público de estadísticas de Home Assistant, para los repositorios que contienen una integración (`custom_components/<dominio>/manifest.json`). Solo cuentan los usuarios que activaron las estadísticas de uso; las tarjetas y los blueprints no tienen cifra.

## Instalación

### 1. El servicio

Copie [`config.example.yaml`](../../config.example.yaml) a `config.yaml` y adáptelo (broker MQTT, directorios de los repositorios).

**Docker** (recomendado) — vea [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml):

```bash
cd deploy
cp ../config.example.yaml config.yaml   # luego edítelo
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Monte los repositorios en la misma ruta que en el host. Ejecute el contenedor con el uid propietario de los repositorios (`user:`); si no, `git fetch` deja archivos que su cuenta no puede modificar.

**Sin Docker** (Python ≥ 3.12): `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, y luego la unidad systemd de usuario [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service).

### 2. La tarjeta

HACS → tres puntos → *Repositorios personalizados* → `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Panel**. Después añada la tarjeta *Git Repo Monitor*: encuentra los repositorios por sí sola.

## Token de GitHub

Cree un token *fine-grained* sobre sus repositorios con los permisos **Metadata** (lectura), **Contents** (lectura), **Actions** (lectura y escritura: la escritura solo sirve para *Relanzar los fallos*) y **Pull requests** (lectura). Páselo con `GITHUB_TOKEN`.

El mismo token se usa para `git fetch`: los remotos SSH (`git@github.com:…`) se reescriben a HTTPS para ese comando, así que no hace falta ninguna clave SSH en el contenedor. Sin token, solo se publica el estado local.

## Webhooks en tiempo real (opcional)

Sin webhooks, un push desde otra máquina o el final de una ejecución programada se ve como mucho en 20 minutos. Con ellos, en unos segundos.

1. Active `webhook` en `config.yaml` con un secreto (o `GRM_WEBHOOK_SECRET`).
2. Exponga el puerto del servicio a través de su proxy inverso, en HTTPS. En un Synology: *Panel de control → Portal de inicio de sesión → Avanzado → Proxy inverso*, origen `https://su.dominio:443` con un nombre de host o un puerto propio, destino `http://<máquina>:8099`. Solo responden `POST /webhook` y `GET /health`, y cada petición se comprueba con su firma HMAC.
3. Cree los webhooks (GitHub CLI con el scope `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://su.dominio/webhook            # todos sus repositorios
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://su.dominio/webhook owner/repo # algunos
```

El script se puede volver a ejecutar: actualiza los webhooks existentes. Vuelva a ejecutarlo cuando cree un repositorio.

## Entidades de Home Assistant

Un dispositivo por repositorio (`<host>` es `host_id`, `<repo>` el nombre del directorio):

| Entidad | Contenido |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; todo el detalle en atributos |
| `binary_sensor.git_<repo>_ci_problem` | un workflow ha fallado (para sus notificaciones) |
| `binary_sensor.git_<repo>_needs_sync` | cambios sin confirmar o commits por subir/bajar |
| `sensor.git_<repo>_version` | versión en la copia de trabajo |
| `sensor.git_<repo>_stars` | estrellas (estadísticas a largo plazo) |
| `sensor.git_<repo>_active_installs` | instalaciones activas (solo integraciones) |
| `sensor.git_<repo>_coverage` | cobertura en % (si existe un informe) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | órdenes |

Además, un dispositivo para el servicio: número de repositorios, cuota restante de la API de GitHub, botón *Refresh all*. Todas las entidades pasan a no disponibles cuando el servicio se detiene (última voluntad MQTT).

## Opciones de la tarjeta

```yaml
type: custom:git-repo-monitor-card
title: Mis repositorios    # opcional
host: alare                # solo esta máquina (por defecto: todas)
sort: status               # status (problemas primero) | name
show_ok: true              # mostrar los repositorios al día
expand_problems: false     # desplegar los repositorios con CI fallida
compact: false             # una línea por repositorio
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Todo se ajusta en el editor visual. Un clic en una fila muestra los workflows fallidos, el último commit, la última release, las instalaciones por versión y los botones *Actualizar*, *Fetch* y *Relanzar los fallos*.

## Topics MQTT

| Topic | Contenido |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retenido, última voluntad) |
| `git_repo_monitor/<host>/<repo>/state` | estado JSON del repositorio (retenido) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | resumen del servicio |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Desarrollo

```bash
pip install -e ".[test]" && pytest     # 100 % de cobertura exigido, informe en reports/
cd card && npm install && npm run build
```

Las versiones se publican con el workflow *Release* (número de versión como entrada): actualiza las versiones, construye la tarjeta adjunta a la release de GitHub y publica la imagen `ghcr.io/elwinmage/ha-git-repo-monitor`.

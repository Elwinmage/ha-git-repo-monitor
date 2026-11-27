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

Este tutorial va de una máquina vacía a una tarjeta que funciona. Cuente unos veinte minutos.

Antes de empezar necesita: la máquina donde están sus copias de trabajo, con **Docker** o **Python ≥ 3.12**; un **broker MQTT** ya conectado a Home Assistant (el complemento Mosquitto sirve perfectamente) y la integración **MQTT** configurada; **HACS** instalado. El token de GitHub es opcional: sin él, la tarjeta muestra igualmente el estado local.

### Paso 1 — Conseguir un acceso MQTT

El servicio se conecta al broker como cualquier otro cliente. Con el complemento Mosquitto, lo más sencillo es un usuario de Home Assistant dedicado: *Ajustes → Personas → Añadir persona*, active *Permitir que la persona inicie sesión*, llámelo `git_monitor`. Anote la dirección del broker (la máquina de Home Assistant, puerto 1883 por defecto) y ese usuario/contraseña.

### Paso 2 — Crear el token de GitHub (recomendado)

En GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories**, o solo los que vigile;
- *Permissions*: **Metadata** (lectura), **Contents** (lectura), **Actions** (lectura **y escritura** — la escritura solo sirve para *Reintentar los fallos*), **Pull requests** (lectura).

Copie en seguida el valor `github_pat_…`, GitHub no lo volverá a mostrar. El mismo token sirve para `git fetch`: los remotos SSH (`git@github.com:…`) se reescriben a HTTPS para ese comando, así que no hace falta clave SSH en el contenedor. Sin token, el servicio escribe `No GitHub token: only the local state is monitored` en sus registros y solo publica el estado local.

### Paso 3 — Escribir la configuración

En la máquina donde están los repositorios:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Tres claves que adaptar en `config.yaml`; el resto puede quedarse igual:

```yaml
host_id: alare                       # nombre de esta máquina en HA (por defecto: el nombre de host)
mqtt:
  host: 192.168.0.109                # su broker (paso 1)
  username: git_monitor              # la contraseña viene del entorno, paso 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # directorio que contiene sus copias de trabajo
```

Se vigila toda copia de trabajo git situada directamente bajo `base_dirs` (`depth: 1`; auméntelo si sus repositorios están en subdirectorios). Los secretos quedan fuera del archivo: se leen del entorno, y [`config.example.yaml`](../../config.example.yaml) documenta todas las demás claves.

### Paso 4 — Arrancar el servicio

**Con Docker** (recomendado). Siempre en `deploy/`, ponga los secretos en un archivo `.env` junto al `docker-compose.yml` y averigüe el uid/gid propietario de los repositorios:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Edite después [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml): esos uid/gid en `user:`, y sus `base_dirs` montados **en la misma ruta dentro y fuera** (así la tarjeta muestra la ruta real del host). Arrancar el contenedor con otro uid es el error clásico: `git fetch` dejaría archivos que su propia cuenta ya no puede modificar.

```bash
docker compose up -d
docker compose logs -f
```

**Sin Docker** (Python ≥ 3.12), con la unidad systemd de usuario [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # mantenerlo activo sin sesión abierta
journalctl --user -u git-repo-monitor -f
```

En ambos casos, los registros deben mostrar una línea `Repository <nombre> found at <ruta>` por repositorio y luego `Watching N git directories`. Una línea `Configuration error: …` significa que `config.yaml` fue rechazado; la tabla del final de esta sección da las causas habituales.

### Paso 5 — Comprobar en Home Assistant

*Ajustes → Dispositivos y servicios → MQTT → dispositivos*: un dispositivo por repositorio, más uno para el propio servicio. Abra uno — `sensor.git_<repo>_status` ya debería contener `ok`, `needs_sync`, `ci_failed`… Si no aparece nada aunque los registros estén limpios, Home Assistant no está leyendo el broker en el que escribe el servicio.

### Paso 6 — Instalar la tarjeta

*HACS → tres puntos (arriba a la derecha) → Repositorios personalizados*: URL `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Dashboard**, *Añadir*. Abra luego *Git Repo Monitor card* y descárguela. Recargue la página (`Ctrl-Mayús-R`) para que el navegador coja el nuevo archivo.

### Paso 7 — Añadir la tarjeta a un panel

*Editar el panel → Añadir tarjeta* y busque *Git Repo Monitor*. Encuentra los repositorios por sí sola; todo lo demás es opcional y se ajusta en el editor visual, véase [Opciones de la tarjeta](#opciones-de-la-tarjeta).

La instalación ha terminado. El paso siguiente es opcional: hace que la parte GitHub de la tarjeta reaccione en segundos en lugar de minutos.

### Paso 8 (opcional) — Webhooks en tiempo real

Sin webhooks, un push desde otra máquina o el final de una ejecución programada se ve como muy tarde en 20 minutos. Con ellos, en unos segundos.

1. En `config.yaml`, ponga `webhook.enabled: true`, añada el secreto en `GRM_WEBHOOK_SECRET` (en `.env` o en el archivo `env`) y reinicie el servicio: el registro muestra `Webhook listening on 0.0.0.0:8099/webhook`.
2. Exponga ese puerto a través de su proxy inverso, en HTTPS. En un Synology: *Panel de control → Portal de inicio de sesión → Avanzado → Proxy inverso*, origen `https://su.dominio:443` con un nombre de host o un puerto dedicado, destino `http://<máquina>:8099`. Solo responden `POST /webhook` y `GET /health`, y cada petición se verifica con su firma HMAC — `curl https://su.dominio/health` debe responder `ok`.
3. Cree los webhooks con GitHub CLI (`gh auth login`, con el ámbito `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://su.dominio/webhook            # todos sus repositorios
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://su.dominio/webhook owner/repo # algunos
```

El script se puede relanzar: actualiza los webhooks existentes. Vuelva a lanzarlo cuando cree un repositorio. En GitHub, *Settings → Webhooks* de un repositorio muestra cada entrega y su respuesta (`202 accepted`).

### Si algo va mal

| Síntoma | Dónde mirar |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | archivo equivocado (`-c`) o `base_dirs` vacío |
| `MQTT connection lost: …` en bucle | dirección del broker, usuario, `GRM_MQTT_PASSWORD` |
| Ningún dispositivo en Home Assistant aunque los registros estén limpios | falta la integración **MQTT**, o `discovery_prefix` distinto de `homeassistant` |
| `Cannot list <directorio>` | `base_dirs` incorrecto, o directorio ilegible para el uid del servicio |
| Ningún repositorio encontrado | copias de trabajo más profundas que `depth`, o patrón `exclude` demasiado amplio |
| Repositorios publicados pero sin datos de GitHub | sin token (`No GitHub token` en los registros) o permisos que faltan |
| `git fetch failed for <repo>` | token sin **Contents** (lectura), o repositorio privado fuera de su alcance |
| `dubious ownership` en los registros | contenedor arrancado con un uid distinto del propietario de los repositorios |
| *Git Repo Monitor* no aparece en la lista de tarjetas | descarga de HACS sin terminar, o página sin recargar |

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

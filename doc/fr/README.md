# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | 🇫🇷 Français | [🇩🇪 Deutsch](../de/README.md) | [🇪🇸 Español](../es/README.md) | [🇮🇹 Italiano](../it/README.md) | [🇵🇱 Polski](../pl/README.md) | [🇵🇹 Português](../pt/README.md)

Une vue d'ensemble de tous vos dépôts git dans Home Assistant : état local, GitHub Actions, releases, étoiles et installations actives. Elle est rafraîchie **seulement quand quelque chose change** : un commit, un push ou la fin d'un workflow.

![Carte](../img/card_fr.png)

Le projet a deux parties dans un même dépôt :

- **un démon** (Python) qui tourne sur la machine où sont vos copies de travail. Il publie tout en MQTT, avec la découverte automatique de Home Assistant ;
- **une carte Lovelace** (`custom:git-repo-monitor-card`), installée par HACS.

## Fonctionnement

| Déclencheur | Ce qui est rafraîchi |
|---|---|
| Changement dans `.git` (inotify) : commit, checkout, merge, fetch… | état local du dépôt, environ 2 s après |
| Toutes les `status_interval` (90 s) | modifications non commitées, rapport de couverture |
| Webhook GitHub (optionnel) | état GitHub du dépôt, puis mode « chaud » |
| Votre flux d'activité GitHub, toutes les 60 s (requêtes conditionnelles gratuites) | dépôts sur lesquels vous avez poussé ou publié |
| Mode « chaud », toutes les 20 s | après un push : fetch + workflows, jusqu'à la fin de tous les runs |
| Toutes les 20 min | rafraîchissement complet (filet de sécurité) |
| Une fois par jour | installations actives (analytics.home-assistant.io), référence quotidienne des étoiles |

Données locales : branche, branche distante, commits à pousser ou à récupérer, modifications non commitées, dernier tag, dernier commit, version du `manifest.json` ou du `package.json`, couverture lue dans `reports/coverage.xml` (format Cobertura : le démon ne lance jamais les tests).

Données GitHub : dernier run de chaque workflow, dernière release et commits depuis, pull requests ouvertes (celles de Dependabot comptées à part), issues, étoiles et forks.

Installations actives : prises dans le fichier public des statistiques de Home Assistant, pour les dépôts qui contiennent une intégration (`custom_components/<domaine>/manifest.json`). Seuls les utilisateurs qui ont activé les statistiques d'usage sont comptés ; les cartes et les blueprints n'ont pas de chiffre.

## Installation

### 1. Le démon

Copiez [`config.example.yaml`](../../config.example.yaml) en `config.yaml` et adaptez-le (broker MQTT, répertoires des dépôts).

**Docker** (recommandé) — voir [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml) :

```bash
cd deploy
cp ../config.example.yaml config.yaml   # puis l'éditer
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Montez les dépôts au même chemin que sur l'hôte. Lancez le conteneur avec l'uid propriétaire des dépôts (`user:`), sinon `git fetch` laisse des fichiers que votre compte ne peut pas modifier.

**Sans Docker** (Python ≥ 3.12) : `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, puis l'unité systemd utilisateur [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service).

### 2. La carte

HACS → trois points → *Dépôts personnalisés* → `https://github.com/Elwinmage/ha-git-repo-monitor`, type **Tableau de bord**. Ajoutez ensuite la carte *Git Repo Monitor* : elle trouve les dépôts toute seule.

## Jeton GitHub

Créez un jeton *fine-grained* sur vos dépôts avec les permissions **Metadata** (lecture), **Contents** (lecture), **Actions** (lecture et écriture : l'écriture ne sert qu'à *Relancer les échecs*) et **Pull requests** (lecture). Passez-le par `GITHUB_TOKEN`.

Le même jeton sert à `git fetch` : les remotes SSH (`git@github.com:…`) sont réécrits en HTTPS pour cette commande, il n'y a donc pas besoin de clé SSH dans le conteneur. Sans jeton, seul l'état local est publié.

## Webhooks temps réel (optionnel)

Sans webhooks, un push depuis une autre machine ou la fin d'un run planifié est vu au plus tard en 20 minutes. Avec, il est vu en quelques secondes.

1. Activez `webhook` dans `config.yaml` avec un secret (ou `GRM_WEBHOOK_SECRET`).
2. Exposez le port du démon par votre reverse proxy, en HTTPS. Sur un Synology : *Panneau de configuration → Portail de connexion → Avancé → Proxy inversé*, source `https://votre.domaine:443` avec un nom d'hôte ou un port dédié, destination `http://<machine>:8099`. Seuls `POST /webhook` et `GET /health` répondent, et chaque requête est vérifiée par sa signature HMAC.
3. Créez les webhooks (GitHub CLI avec le scope `admin:repo_hook`) :

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://votre.domaine/webhook            # tous vos dépôts
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://votre.domaine/webhook owner/repo # certains
```

Le script peut être relancé : il met à jour les webhooks existants. Relancez-le quand vous créez un dépôt.

## Entités Home Assistant

Un appareil par dépôt (`<host>` est `host_id`, `<repo>` le nom du répertoire) :

| Entité | Contenu |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown` ; tout le détail en attributs |
| `binary_sensor.git_<repo>_ci_problem` | un workflow a échoué (pour vos notifications) |
| `binary_sensor.git_<repo>_needs_sync` | modifications non commitées ou commits à pousser/récupérer |
| `sensor.git_<repo>_version` | version dans la copie de travail |
| `sensor.git_<repo>_stars` | étoiles (statistiques long terme) |
| `sensor.git_<repo>_active_installs` | installations actives (intégrations seulement) |
| `sensor.git_<repo>_coverage` | couverture en % (si un rapport existe) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | commandes |

Plus un appareil pour le démon : nombre de dépôts, quota d'API GitHub restant, bouton *Refresh all*. Toutes les entités deviennent indisponibles quand le démon s'arrête (dernière volonté MQTT).

## Options de la carte

```yaml
type: custom:git-repo-monitor-card
title: Mes dépôts          # optionnel
host: alare                # seulement cette machine (défaut : toutes)
sort: status               # status (problèmes en premier) | name
show_ok: true              # afficher les dépôts à jour
expand_problems: false     # déplier les dépôts dont la CI a échoué
compact: false             # une ligne par dépôt
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Tout se règle dans l'éditeur visuel. Un clic sur une ligne affiche les workflows en échec, le dernier commit, la dernière release, les installations par version et les boutons *Rafraîchir*, *Fetch* et *Relancer les échecs*.

## Topics MQTT

| Topic | Contenu |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retenu, dernière volonté) |
| `git_repo_monitor/<host>/<repo>/state` | état JSON du dépôt (retenu) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | résumé du démon |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Développement

```bash
pip install -e ".[test]" && pytest     # 100 % de couverture exigés, rapport dans reports/
cd card && npm install && npm run build
```

Les versions se publient avec le workflow *Release* (numéro de version en entrée) : il met à jour les versions, construit la carte jointe à la release GitHub et publie l'image `ghcr.io/elwinmage/ha-git-repo-monitor`.

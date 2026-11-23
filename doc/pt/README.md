# ha-git-repo-monitor

[🇬🇧 English](../../README.md) | [🇫🇷 Français](../fr/README.md) | [🇩🇪 Deutsch](../de/README.md) | [🇪🇸 Español](../es/README.md) | [🇮🇹 Italiano](../it/README.md) | [🇵🇱 Polski](../pl/README.md) | 🇵🇹 Português

Uma visão geral de todos os seus repositórios git no Home Assistant: estado local, GitHub Actions, releases, estrelas e instalações ativas. É atualizada **apenas quando algo muda**: um commit, um push ou o fim de um workflow.

![Cartão](../img/card.png)

O projeto tem duas partes num único repositório:

- **um serviço** (Python) que corre na máquina onde estão as suas cópias de trabalho. Publica tudo por MQTT com a descoberta automática do Home Assistant;
- **um cartão Lovelace** (`custom:git-repo-monitor-card`), instalado pelo HACS.

## Funcionamento

| Gatilho | O que é atualizado |
|---|---|
| Alteração em `.git` (inotify): commit, checkout, merge, fetch… | estado local do repositório, cerca de 2 s depois |
| A cada `status_interval` (90 s) | alterações por fazer commit, relatório de cobertura |
| Webhook do GitHub (opcional) | estado GitHub do repositório, depois modo «quente» |
| O seu feed de atividade do GitHub, a cada 60 s (pedidos condicionais gratuitos) | repositórios onde fez push ou publicou uma release |
| Modo «quente», a cada 20 s | após um push: fetch + workflows, até todas as execuções terminarem |
| A cada 20 min | atualização completa (rede de segurança) |
| Uma vez por dia | instalações ativas (analytics.home-assistant.io), referência diária das estrelas |

Dados locais: ramo, ramo remoto, commits por enviar ou receber, alterações por fazer commit, última tag, último commit, versão do `manifest.json` ou do `package.json`, cobertura lida em `reports/coverage.xml` (formato Cobertura: o serviço nunca executa os testes).

Dados do GitHub: última execução de cada workflow, última release e commits desde então, pull requests abertos (os do Dependabot contados à parte), issues, estrelas e forks.

Instalações ativas: retiradas do ficheiro público de estatísticas do Home Assistant, para os repositórios que contêm uma integração (`custom_components/<domínio>/manifest.json`). Só contam os utilizadores que ativaram as estatísticas de utilização; cartões e blueprints não têm número.

## Instalação

### 1. O serviço

Copie [`config.example.yaml`](../../config.example.yaml) para `config.yaml` e adapte-o (broker MQTT, pastas dos repositórios).

**Docker** (recomendado) — ver [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml):

```bash
cd deploy
cp ../config.example.yaml config.yaml   # depois edite-o
GITHUB_TOKEN=github_pat_xxx GRM_MQTT_PASSWORD=xxx docker compose up -d
```

Monte os repositórios no mesmo caminho que no anfitrião. Execute o contentor com o uid dono dos repositórios (`user:`); caso contrário, `git fetch` deixa ficheiros que a sua conta não consegue modificar.

**Sem Docker** (Python ≥ 3.12): `pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor`, depois a unidade systemd de utilizador [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service).

### 2. O cartão

HACS → três pontos → *Repositórios personalizados* → `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Painel**. Depois adicione o cartão *Git Repo Monitor*: encontra os repositórios sozinho.

## Token do GitHub

Crie um token *fine-grained* sobre os seus repositórios com as permissões **Metadata** (leitura), **Contents** (leitura), **Actions** (leitura e escrita: a escrita só serve para *Relançar as falhas*) e **Pull requests** (leitura). Passe-o através de `GITHUB_TOKEN`.

O mesmo token é usado para `git fetch`: os remotos SSH (`git@github.com:…`) são reescritos em HTTPS para esse comando, por isso não é precisa nenhuma chave SSH no contentor. Sem token, só o estado local é publicado.

## Webhooks em tempo real (opcional)

Sem webhooks, um push a partir de outra máquina ou o fim de uma execução agendada é visto no máximo em 20 minutos. Com eles, em poucos segundos.

1. Ative `webhook` em `config.yaml` com um segredo (ou `GRM_WEBHOOK_SECRET`).
2. Exponha a porta do serviço através do seu reverse proxy, em HTTPS. Num Synology: *Painel de controlo → Portal de início de sessão → Avançado → Proxy inverso*, origem `https://seu.dominio:443` com um nome de anfitrião ou uma porta próprios, destino `http://<máquina>:8099`. Só respondem `POST /webhook` e `GET /health`, e cada pedido é verificado pela sua assinatura HMAC.
3. Crie os webhooks (GitHub CLI com o scope `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://seu.dominio/webhook            # todos os seus repositórios
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://seu.dominio/webhook owner/repo # alguns
```

O script pode ser executado de novo: atualiza os webhooks existentes. Execute-o de novo quando criar um repositório.

## Entidades do Home Assistant

Um dispositivo por repositório (`<host>` é `host_id`, `<repo>` o nome da pasta):

| Entidade | Conteúdo |
|---|---|
| `sensor.git_<repo>_status` | `ok`, `ci_failed`, `ci_running`, `needs_sync`, `no_upstream`, `unknown`; todo o detalhe em atributos |
| `binary_sensor.git_<repo>_ci_problem` | um workflow falhou (para as suas notificações) |
| `binary_sensor.git_<repo>_needs_sync` | alterações por fazer commit ou commits por enviar/receber |
| `sensor.git_<repo>_version` | versão na cópia de trabalho |
| `sensor.git_<repo>_stars` | estrelas (estatísticas de longo prazo) |
| `sensor.git_<repo>_active_installs` | instalações ativas (apenas integrações) |
| `sensor.git_<repo>_coverage` | cobertura em % (se existir um relatório) |
| `button.git_<repo>_refresh` / `_fetch` / `_rerun_failed` | comandos |

Mais um dispositivo para o serviço: número de repositórios, quota restante da API do GitHub, botão *Refresh all*. Todas as entidades ficam indisponíveis quando o serviço para (last will MQTT).

## Opções do cartão

```yaml
type: custom:git-repo-monitor-card
title: Os meus repositórios  # opcional
host: alare                  # só esta máquina (predefinição: todas)
sort: status                 # status (problemas primeiro) | name
show_ok: true                # mostrar os repositórios atualizados
expand_problems: false       # expandir os repositórios com CI falhada
compact: false               # uma linha por repositório
show_version: true
show_ci: true
show_coverage: true
show_stars: true
show_installs: true
```

Tudo se ajusta no editor visual. Um clique numa linha mostra os workflows falhados, o último commit, a última release, as instalações por versão e os botões *Atualizar*, *Fetch* e *Relançar as falhas*.

## Tópicos MQTT

| Tópico | Conteúdo |
|---|---|
| `git_repo_monitor/<host>/availability` | `online` / `offline` (retido, last will) |
| `git_repo_monitor/<host>/<repo>/state` | estado JSON do repositório (retido) |
| `git_repo_monitor/<host>/<repo>/cmd` | `refresh`, `fetch`, `rerun_failed` |
| `git_repo_monitor/<host>/bridge` | resumo do serviço |
| `git_repo_monitor/<host>/cmd` | `refresh_all` |

## Desenvolvimento

```bash
pip install -e ".[test]" && pytest     # 100 % de cobertura exigidos, relatório em reports/
cd card && npm install && npm run build
```

As versões publicam-se com o workflow *Release* (número de versão como entrada): atualiza as versões, compila o cartão anexado à release do GitHub e publica a imagem `ghcr.io/elwinmage/ha-git-repo-monitor`.

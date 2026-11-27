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

Este tutorial vai de uma máquina vazia até um cartão a funcionar. Conte cerca de vinte minutos.

Antes de começar são necessários: a máquina onde estão as suas cópias de trabalho, com **Docker** ou **Python ≥ 3.12**; um **broker MQTT** já ligado ao Home Assistant (o add-on Mosquitto serve perfeitamente) e a integração **MQTT** configurada; **HACS** instalado. O token do GitHub é opcional: sem ele, o cartão mostra ainda assim o estado local.

### Passo 1 — Obter um acesso MQTT

O serviço liga-se ao broker como qualquer outro cliente. Com o add-on Mosquitto, o mais simples é um utilizador dedicado do Home Assistant: *Configurações → Pessoas → Adicionar pessoa*, ative *Permitir que a pessoa inicie sessão*, chame-lhe `git_monitor`. Anote o endereço do broker (a máquina do Home Assistant, porta 1883 por omissão) e esse utilizador/palavra-passe.

### Passo 2 — Criar o token do GitHub (recomendado)

No GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.

- *Repository access*: **All repositories**, ou apenas os que vigia;
- *Permissions*: **Metadata** (leitura), **Contents** (leitura), **Actions** (leitura **e escrita** — a escrita só serve para *Repetir as falhas*), **Pull requests** (leitura).

Copie logo o valor `github_pat_…`, o GitHub nunca o mostrará outra vez. O mesmo token serve para o `git fetch`: os remotos SSH (`git@github.com:…`) são reescritos em HTTPS para esse comando, pelo que não é preciso chave SSH no contentor. Sem token, o serviço escreve `No GitHub token: only the local state is monitored` nos registos e publica apenas o estado local.

### Passo 3 — Escrever a configuração

Na máquina onde estão os repositórios:

```bash
git clone https://github.com/Elwinmage/ha-git-repo-monitor
cd ha-git-repo-monitor/deploy
cp ../config.example.yaml config.yaml
```

Três chaves a adaptar em `config.yaml`, o resto pode ficar como está:

```yaml
host_id: alare                       # nome desta máquina no HA (por omissão: o nome do host)
mqtt:
  host: 192.168.0.109                # o seu broker (passo 1)
  username: git_monitor              # a palavra-passe vem do ambiente, passo 4
repos:
  base_dirs:
    - /srv/appdata/homeassistant/git  # diretório que contém as suas cópias de trabalho
```

É vigiada toda a cópia de trabalho git diretamente sob `base_dirs` (`depth: 1`; aumente-o se os seus repositórios estiverem em subdiretórios). Os segredos ficam fora do ficheiro: são lidos do ambiente, e o [`config.example.yaml`](../../config.example.yaml) documenta todas as outras chaves.

### Passo 4 — Arrancar o serviço

**Com Docker** (recomendado). Ainda em `deploy/`, ponha os segredos num ficheiro `.env` ao lado do `docker-compose.yml` e descubra o uid/gid proprietário dos repositórios:

```bash
cat > .env <<'EOF'
GITHUB_TOKEN=github_pat_xxx
GRM_MQTT_PASSWORD=xxx
EOF
chmod 600 .env
id -u ; id -g
```

Edite depois o [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml): esses uid/gid em `user:`, e as suas `base_dirs` montadas **no mesmo caminho dentro e fora** (o cartão mostra então o caminho real do host). Arrancar o contentor com outro uid é o erro clássico: o `git fetch` deixaria ficheiros que a sua própria conta já não pode alterar.

```bash
docker compose up -d
docker compose logs -f
```

**Sem Docker** (Python ≥ 3.12), com a unidade systemd de utilizador [`deploy/git-repo-monitor.service`](../../deploy/git-repo-monitor.service):

```bash
pipx install git+https://github.com/Elwinmage/ha-git-repo-monitor
mkdir -p ~/.config/git-repo-monitor ~/.config/systemd/user
cp config.yaml ~/.config/git-repo-monitor/config.yaml
printf 'GITHUB_TOKEN=github_pat_xxx\nGRM_MQTT_PASSWORD=xxx\n' > ~/.config/git-repo-monitor/env
chmod 600 ~/.config/git-repo-monitor/env
cp git-repo-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now git-repo-monitor
loginctl enable-linger "$USER"   # mantê-lo ativo sem sessão aberta
journalctl --user -u git-repo-monitor -f
```

Nos dois casos, os registos devem mostrar uma linha `Repository <nome> found at <caminho>` por repositório e depois `Watching N git directories`. Uma linha `Configuration error: …` significa que o `config.yaml` foi recusado: a tabela no fim desta secção dá as causas habituais.

### Passo 5 — Verificar no Home Assistant

*Configurações → Dispositivos e serviços → MQTT → dispositivos*: um dispositivo por repositório, mais um para o próprio serviço. Abra um deles — `sensor.git_<repo>_status` já deve conter `ok`, `needs_sync`, `ci_failed`… Se nada aparecer estando os registos limpos, o Home Assistant não está a ler o broker onde o serviço escreve.

### Passo 6 — Instalar o cartão

*HACS → três pontos (canto superior direito) → Repositórios personalizados*: URL `https://github.com/Elwinmage/ha-git-repo-monitor`, tipo **Dashboard**, *Adicionar*. Abra depois *Git Repo Monitor card* e descarregue-o. Recarregue a página (`Ctrl-Shift-R`) para que o navegador pegue no novo ficheiro.

### Passo 7 — Adicionar o cartão a um painel

*Editar painel → Adicionar cartão* e procure *Git Repo Monitor*. Encontra os repositórios sozinho; tudo o resto é opcional e regula-se no editor visual, ver [Opções do cartão](#opções-do-cartão).

A instalação está feita. O passo seguinte é opcional: faz a parte GitHub do cartão reagir em segundos em vez de minutos.

### Passo 8 (opcional) — Webhooks em tempo real

Sem webhooks, um push de outra máquina ou o fim de uma execução agendada é visto no máximo em 20 minutos. Com eles, em alguns segundos.

1. Em `config.yaml`, ponha `webhook.enabled: true`, acrescente o segredo em `GRM_WEBHOOK_SECRET` (no `.env` ou no ficheiro `env`) e reinicie o serviço: o registo mostra `Webhook listening on 0.0.0.0:8099/webhook`.
2. Exponha essa porta através do seu proxy inverso, em HTTPS. Num Synology: *Painel de controlo → Portal de início de sessão → Avançado → Proxy inverso*, origem `https://seu.dominio:443` com um nome de host ou uma porta dedicados, destino `http://<máquina>:8099`. Só `POST /webhook` e `GET /health` respondem, e cada pedido é verificado pela sua assinatura HMAC — `curl https://seu.dominio/health` deve responder `ok`.
3. Crie os webhooks com o GitHub CLI (`gh auth login`, com o âmbito `admin:repo_hook`):

```bash
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://seu.dominio/webhook            # todos os seus repositórios
GRM_WEBHOOK_SECRET=xxx scripts/setup_webhooks.sh https://seu.dominio/webhook owner/repo # alguns
```

O script pode voltar a ser executado: atualiza os webhooks existentes. Execute-o de novo quando criar um repositório. No GitHub, *Settings → Webhooks* de um repositório mostra cada entrega e a sua resposta (`202 accepted`).

### Se algo não correr bem

| Sintoma | Onde olhar |
|---|---|
| `Configuration error: repos.base_dirs must list at least one directory` | ficheiro errado (`-c`) ou `base_dirs` deixado vazio |
| `MQTT connection lost: …` em ciclo | endereço do broker, utilizador, `GRM_MQTT_PASSWORD` |
| Nenhum dispositivo no Home Assistant com os registos limpos | integração **MQTT** ausente, ou `discovery_prefix` diferente de `homeassistant` |
| `Cannot list <diretório>` | `base_dirs` errado, ou diretório ilegível para o uid do serviço |
| Nenhum repositório encontrado | cópias de trabalho mais profundas do que `depth`, ou padrão `exclude` demasiado amplo |
| Repositórios publicados mas sem dados do GitHub | sem token (`No GitHub token` nos registos) ou permissões em falta |
| `git fetch failed for <repo>` | token sem **Contents** (leitura), ou repositório privado fora do seu âmbito |
| `dubious ownership` nos registos | contentor arrancado com um uid diferente do proprietário dos repositórios |
| *Git Repo Monitor* ausente da lista de cartões | descarga do HACS não terminada, ou página não recarregada |

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

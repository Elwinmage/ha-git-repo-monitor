import { LitElement, html, css, nothing, type PropertyValues, type TemplateResult } from "lit";
import { translator } from "./i18n";
// Importing the editor also registers <git-repo-monitor-card-editor>.
import { configSchema } from "./editor";
import { MONITOR_ID, repoRank, type CardConfig, type Hass, type RepoPayload } from "./types";

const VERSION = "0.1.0";
const STATUS_ENTITY_RE = /^sensor\.git_.+_status$/;

// Lower is shown first when sorting by status.
const SEVERITY: Record<string, number> = {
  ci_failed: 0,
  offline: 1,
  needs_sync: 2,
  ci_running: 3,
  no_upstream: 4,
  unknown: 5,
  ok: 6,
};
const FAILED = new Set(["failure", "cancelled", "startup_failure", "timed_out"]);
const SUCCESS = new Set(["success", "skipped", "neutral"]);

interface Row {
  entityId: string;
  status: string;
  offline: boolean;
  data: RepoPayload;
}

const DEFAULTS: Required<Omit<CardConfig, "type" | "title" | "host" | "repos">> = {
  sort: "status",
  show_ok: true,
  expand_problems: false,
  compact: false,
  show_version: true,
  show_ci: true,
  show_coverage: true,
  show_stars: true,
  show_installs: true,
};

function globalTranslator(): (key: string) => string {
  const ha = document.querySelector("home-assistant") as unknown as { hass?: Hass } | null;
  return translator(ha?.hass?.locale?.language ?? ha?.hass?.language ?? navigator.language);
}

function relativeTime(iso: string | null | undefined, language: string): string {
  if (!iso) return "";
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  if (Number.isNaN(seconds)) return "";
  const format = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(Math.round(seconds), "second");
}

class GitRepoMonitorCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _expanded: { state: true },
  };

  hass?: Hass;
  private _config!: CardConfig;
  private _expanded: Set<string> = new Set();
  // Last known payload per entity, to keep showing data while the daemon is offline.
  private _cache = new Map<string, RepoPayload>();

  static getStubConfig(): Partial<CardConfig> {
    return {};
  }

  static getConfigElement(): HTMLElement {
    return document.createElement("git-repo-monitor-card-editor");
  }

  static getConfigForm() {
    const t = globalTranslator();
    return {
      schema: configSchema(t),
      computeLabel: (schema: { name: string }) => t(`editor.${schema.name}`),
      assertConfig: (config: CardConfig) => {
        if (config.sort && !["status", "name"].includes(config.sort)) throw new Error("sort: status | name");
      },
    };
  }

  setConfig(config: CardConfig): void {
    if (!config) throw new Error("Invalid configuration");
    if (config.repos !== undefined && !(Array.isArray(config.repos) && config.repos.every((r) => typeof r === "string")))
      throw new Error('repos: list of "<host>/<repo>" or "<repo>" names');
    this._config = { ...DEFAULTS, ...config };
  }

  getCardSize(): number {
    return 2 + Math.ceil(this._rows().length * 0.8);
  }

  private get _t(): (key: string) => string {
    return translator(this._language);
  }

  private get _language(): string {
    return this.hass?.locale?.language ?? this.hass?.language ?? "en";
  }

  private _rows(): Row[] {
    if (!this.hass) return [];
    const rows: Row[] = [];
    for (const entity of Object.values(this.hass.states)) {
      const marked = entity.attributes?.monitor === MONITOR_ID;
      if (!marked && !STATUS_ENTITY_RE.test(entity.entity_id)) continue;
      if (marked) this._cache.set(entity.entity_id, entity.attributes as RepoPayload);
      const data = this._cache.get(entity.entity_id);
      if (!data) continue;
      if (this._config.host && data.host !== this._config.host) continue;
      const offline = entity.state === "unavailable" || !marked;
      rows.push({ entityId: entity.entity_id, offline, status: offline ? "offline" : data.status, data });
    }
    const visible = this._config.show_ok ? rows : rows.filter((r) => r.status !== "ok");
    const selection = this._config.repos ?? [];
    if (selection.length) {
      // Only the configured repositories, in the configured order: "sort" does not apply.
      return visible
        .map((row) => ({ row, rank: repoRank(row.data, selection) }))
        .filter((item) => item.rank >= 0)
        .sort((a, b) => a.rank - b.rank)
        .map((item) => item.row);
    }
    return visible.sort((a, b) => {
      if (this._config.sort === "status") {
        const diff = (SEVERITY[a.status] ?? 9) - (SEVERITY[b.status] ?? 9);
        if (diff) return diff;
      }
      return a.data.name.localeCompare(b.data.name);
    });
  }

  protected willUpdate(changed: PropertyValues): void {
    if (changed.has("hass") && this._config?.expand_problems) {
      for (const row of this._rows()) {
        const key = `auto:${row.entityId}`;
        if (row.status === "ci_failed" && !this._expanded.has(key)) {
          this._expanded.add(key);
          this._expanded.add(row.entityId);
        } else if (row.status !== "ci_failed" && this._expanded.has(key)) {
          this._expanded.delete(key);
        }
      }
    }
  }

  private _toggle(entityId: string): void {
    const next = new Set(this._expanded);
    if (next.has(entityId)) next.delete(entityId);
    else next.add(entityId);
    this._expanded = next;
  }

  private async _send(topic: string, payload: string): Promise<void> {
    if (!this.hass) return;
    await this.hass.callService("mqtt", "publish", { topic, payload });
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message: `${this._t("sent")} : ${this._t(payload)}` },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private _refreshAll(rows: Row[]): void {
    const topics = new Set(rows.map((r) => r.data.cmd_topic.replace(/\/[^/]+\/cmd$/, "/cmd")));
    topics.forEach((topic) => this._send(topic, "refresh_all"));
  }

  protected render(): TemplateResult {
    if (!this._config) return html``;
    const t = this._t;
    const rows = this._rows();
    const okCount = rows.filter((r) => r.status === "ok").length;
    const problems = rows.length - okCount;
    return html`
      <ha-card>
        <div class="header">
          <div class="title">${this._config.title ?? t("title")}</div>
          <div class="summary">
            ${okCount ? html`<span class="pill ok">${okCount} ${t("ok")}</span>` : nothing}
            ${problems ? html`<span class="pill warn">${problems} ${t("problems")}</span>` : nothing}
          </div>
          ${rows.length
            ? html`<button class="icon" title=${t("refresh_all")} @click=${() => this._refreshAll(rows)}>
                <ha-icon icon="mdi:refresh"></ha-icon>
              </button>`
            : nothing}
        </div>
        <div class="rows ${this._config.compact ? "compact" : ""}">
          ${rows.length ? rows.map((row) => this._renderRow(row)) : html`<div class="empty">${t("no_repos")}</div>`}
        </div>
      </ha-card>
    `;
  }

  private _renderRow(row: Row): TemplateResult {
    const t = this._t;
    const cfg = this._config;
    const { data } = row;
    const local = data.local ?? {};
    const remote = data.remote ?? {};
    const expanded = this._expanded.has(row.entityId);
    const ci = data.ci ?? { total: 0, ok: 0, failed: 0, running: 0 };
    const ciClass = ci.failed ? "bad" : ci.running ? "run" : ci.ok === ci.total ? "good" : "warn";
    const unreleased = remote.unreleased_commits ?? 0;
    const coverage = local.coverage;

    return html`
      <div class="row ${row.status} ${row.offline ? "offline" : ""}">
        <div class="main" @click=${() => this._toggle(row.entityId)}>
          <span class="dot" title=${t(`status.${row.status}`)}></span>
          <div class="ident">
            ${data.html_url
              ? html`<a class="name" href=${data.html_url} target="_blank" rel="noreferrer" @click=${(e: Event) =>
                  e.stopPropagation()}>${data.name}</a>`
              : html`<span class="name">${data.name}</span>`}
            <div class="sub">
              ${local.branch
                ? html`<span class="branch ${local.branch === "main" || local.branch === "master" ? "main" : ""}"
                    ><ha-icon icon="mdi:source-branch"></ha-icon>${local.branch}</span
                  >`
                : nothing}
              ${local.dirty
                ? html`<span class="flag warn" title=${t("dirty")}
                    ><ha-icon icon="mdi:pencil"></ha-icon>${local.dirty_count}</span
                  >`
                : nothing}
              ${local.ahead
                ? html`<span class="flag warn" title=${t("ahead")}>↑${local.ahead}</span>`
                : nothing}
              ${local.behind
                ? html`<span class="flag warn" title=${t("behind")}>↓${local.behind}</span>`
                : nothing}
              ${remote.open_prs
                ? html`<span class="flag" title=${t("prs")}
                    ><ha-icon icon="mdi:source-pull"></ha-icon>${remote.open_prs}</span
                  >`
                : nothing}
              ${row.status !== "ok" ? html`<span class="status-label">${t(`status.${row.status}`)}</span>` : nothing}
            </div>
          </div>
          <div class="metrics">
            ${cfg.show_version && data.version
              ? html`<span class="metric version" title=${unreleased ? `${unreleased} ${t("unreleased")}` : ""}
                  >${data.version}${unreleased ? html`<sup>+${unreleased}</sup>` : nothing}</span
                >`
              : nothing}
            ${cfg.show_ci && ci.total
              ? html`<span class="metric ci ${ciClass}">${ci.ok}/${ci.total}</span>`
              : nothing}
            ${cfg.show_coverage && coverage != null
              ? html`<span class="metric cov ${coverage >= 100 ? "full" : "partial"}" title=${t("coverage")}
                  >${Math.round(coverage)}%</span
                >`
              : nothing}
            ${cfg.show_stars && data.stars != null
              ? html`<span class="metric stars" title=${t("stars")}
                  ><ha-icon icon="mdi:star"></ha-icon>${data.stars}${data.stars_delta
                    ? html`<sup class="delta">${data.stars_delta > 0 ? "+" : ""}${data.stars_delta}</sup>`
                    : nothing}</span
                >`
              : nothing}
            ${cfg.show_installs && data.installs
              ? html`<span class="metric installs" title=${t("installs")}
                  ><ha-icon icon="mdi:download-network"></ha-icon>${data.installs.total}</span
                >`
              : nothing}
          </div>
          <ha-icon class="chevron" icon=${expanded ? "mdi:chevron-up" : "mdi:chevron-down"}></ha-icon>
        </div>
        ${expanded ? this._renderDetails(row) : nothing}
      </div>
    `;
  }

  private _renderDetails(row: Row): TemplateResult {
    const t = this._t;
    const lang = this._language;
    const { data } = row;
    const local = data.local ?? {};
    const remote = data.remote ?? {};
    const workflows = (remote.workflows ?? []).filter((w) => !SUCCESS.has(w.state));
    const versions = Object.entries(data.installs?.versions ?? {}).slice(0, 5);
    const release = remote.latest_release;

    return html`
      <div class="details">
        ${remote.available
          ? html`<div class="section">
              <div class="label">${t("workflows")}</div>
              ${workflows.length
                ? workflows.map(
                    (w) => html`<div class="workflow">
                      <span class="state ${FAILED.has(w.state) ? "bad" : "run"}">${w.state}</span>
                      ${w.url
                        ? html`<a href=${w.url} target="_blank" rel="noreferrer">${w.name}</a>`
                        : html`<span>${w.name}</span>`}
                      <span class="muted">${w.branch ?? ""}${w.event ? ` · ${w.event}` : ""}</span>
                    </div>`,
                  )
                : html`<div class="muted">${t("all_green")}</div>`}
            </div>`
          : nothing}
        <div class="grid">
          ${local.last_commit
            ? html`<div class="label">${t("last_commit")}</div>
                <div>
                  <code>${local.last_commit.sha.slice(0, 7)}</code> ${local.last_commit.subject}
                  <span class="muted">· ${relativeTime(local.last_commit.date, lang)}</span>
                </div>`
            : nothing}
          ${remote.available
            ? html`<div class="label">${t("release")}</div>
                <div>
                  ${release
                    ? html`<a href=${release.url} target="_blank" rel="noreferrer">${release.tag}</a>
                        <span class="muted">· ${relativeTime(release.published_at, lang)}</span>
                        ${remote.unreleased_commits
                          ? html`<span class="flag warn">+${remote.unreleased_commits} ${t("unreleased")}</span>`
                          : nothing}`
                    : html`<span class="muted">${t("no_release")}</span>`}
                </div>
                <div class="label">GitHub</div>
                <div>
                  ${remote.open_prs ?? 0} ${t("prs")}${remote.dependabot_prs
                    ? ` (${remote.dependabot_prs} ${t("dependabot")})`
                    : ""}
                  · ${remote.open_issues ?? 0} ${t("issues")}
                </div>`
            : nothing}
          ${versions.length
            ? html`<div class="label">${t("versions")}</div>
                <div class="versions">
                  ${versions.map(([v, n]) => html`<span class="chip">${v} <b>${n}</b></span>`)}
                </div>`
            : nothing}
          <div class="label">${t("path")}</div>
          <div class="muted">${data.host}:${data.path}</div>
          <div class="label">${t("updated")}</div>
          <div class="muted">${relativeTime(data.updated_at, lang)}</div>
        </div>
        <div class="actions">
          <button ?disabled=${row.offline} @click=${() => this._send(data.cmd_topic, "refresh")}>
            <ha-icon icon="mdi:refresh"></ha-icon>${t("refresh")}
          </button>
          <button ?disabled=${row.offline} @click=${() => this._send(data.cmd_topic, "fetch")}>
            <ha-icon icon="mdi:cloud-download"></ha-icon>${t("fetch")}
          </button>
          ${data.full_name
            ? html`<button
                ?disabled=${row.offline || !data.ci?.failed}
                @click=${() => this._send(data.cmd_topic, "rerun_failed")}
              >
                <ha-icon icon="mdi:replay"></ha-icon>${t("rerun_failed")}
              </button>`
            : nothing}
        </div>
      </div>
    `;
  }

  static styles = css`
    :host {
      --grm-ok: var(--success-color, #43a047);
      --grm-bad: var(--error-color, #db4437);
      --grm-warn: var(--warning-color, #ffa600);
      --grm-run: var(--info-color, #039be5);
      --grm-muted: var(--secondary-text-color, #727272);
    }
    ha-card {
      color: var(--primary-text-color);
    }
    .header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 16px 4px;
    }
    .title {
      font-size: 1.2em;
      font-weight: 500;
      flex: 1;
    }
    .summary {
      display: flex;
      gap: 6px;
    }
    .pill {
      font-size: 0.8em;
      padding: 2px 8px;
      border-radius: 10px;
      color: #fff;
    }
    .pill.ok {
      background: var(--grm-ok);
    }
    .pill.warn {
      background: var(--grm-bad);
    }
    button.icon {
      background: none;
      border: none;
      color: var(--primary-text-color);
      cursor: pointer;
      padding: 4px;
      border-radius: 50%;
    }
    button.icon:hover {
      background: var(--secondary-background-color);
    }
    .rows {
      padding: 4px 8px 8px;
    }
    .empty {
      padding: 16px;
      color: var(--grm-muted);
    }
    .row {
      border-radius: 8px;
      margin: 2px 0;
    }
    .row:hover {
      background: var(--secondary-background-color);
    }
    .main {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px;
      cursor: pointer;
    }
    .compact .main {
      padding: 4px 8px;
    }
    .compact .sub {
      display: none;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex: none;
      background: var(--grm-muted);
    }
    .ok .dot {
      background: var(--grm-ok);
    }
    .ci_failed .dot {
      background: var(--grm-bad);
    }
    .needs_sync .dot {
      background: var(--grm-warn);
    }
    .ci_running .dot {
      background: var(--grm-run);
      animation: pulse 1.2s ease-in-out infinite;
    }
    .offline {
      opacity: 0.6;
    }
    @keyframes pulse {
      50% {
        opacity: 0.3;
      }
    }
    .ident {
      flex: 1;
      min-width: 0;
    }
    .name {
      font-weight: 500;
      color: var(--primary-text-color);
      text-decoration: none;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      display: block;
    }
    a.name:hover {
      text-decoration: underline;
    }
    .sub {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      font-size: 0.8em;
      color: var(--grm-muted);
      align-items: center;
    }
    ha-icon {
      --mdc-icon-size: 14px;
      vertical-align: -2px;
      margin-right: 2px;
    }
    .branch.main {
      color: var(--grm-warn);
    }
    .flag.warn {
      color: var(--grm-warn);
    }
    .status-label {
      font-weight: 500;
    }
    .ci_failed .status-label {
      color: var(--grm-bad);
    }
    .ci_running .status-label {
      color: var(--grm-run);
    }
    .needs_sync .status-label {
      color: var(--grm-warn);
    }
    .metrics {
      display: flex;
      gap: 10px;
      align-items: center;
      font-size: 0.85em;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .metric {
      white-space: nowrap;
    }
    .metric sup {
      font-size: 0.75em;
      color: var(--grm-warn);
      margin-left: 1px;
    }
    .metric sup.delta {
      color: var(--grm-ok);
    }
    .version {
      font-family: var(--code-font-family, monospace);
    }
    .ci {
      padding: 1px 6px;
      border-radius: 8px;
      color: #fff;
      font-weight: 500;
    }
    .ci.good {
      background: var(--grm-ok);
    }
    .ci.bad {
      background: var(--grm-bad);
    }
    .ci.run {
      background: var(--grm-run);
    }
    .ci.warn {
      background: var(--grm-warn);
    }
    .cov.full {
      color: var(--grm-run);
    }
    .cov.partial {
      color: var(--grm-warn);
    }
    .stars ha-icon {
      color: #f5b800;
    }
    .chevron {
      --mdc-icon-size: 20px;
      color: var(--grm-muted);
    }
    .details {
      padding: 4px 12px 12px 28px;
      font-size: 0.9em;
    }
    .section {
      margin-bottom: 8px;
    }
    .label {
      color: var(--grm-muted);
      font-size: 0.85em;
    }
    .grid {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 4px 12px;
      align-items: baseline;
    }
    .workflow {
      display: flex;
      gap: 8px;
      align-items: baseline;
      padding: 2px 0;
    }
    .workflow a,
    .grid a {
      color: var(--primary-color);
    }
    .state {
      font-size: 0.8em;
      padding: 0 6px;
      border-radius: 6px;
      color: #fff;
    }
    .state.bad {
      background: var(--grm-bad);
    }
    .state.run {
      background: var(--grm-warn);
    }
    .muted {
      color: var(--grm-muted);
    }
    code {
      font-size: 0.9em;
    }
    .versions {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .chip {
      background: var(--secondary-background-color);
      border-radius: 6px;
      padding: 0 6px;
      font-size: 0.85em;
    }
    .actions {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    .actions button {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: none;
      border: 1px solid var(--divider-color, #ccc);
      color: var(--primary-color);
      border-radius: 16px;
      padding: 4px 12px;
      cursor: pointer;
      font: inherit;
      font-size: 0.85em;
    }
    .actions button:disabled {
      opacity: 0.4;
      cursor: default;
    }
  `;
}

customElements.define("git-repo-monitor-card", GitRepoMonitorCard);

(window as any).customCards = (window as any).customCards || [];
(window as any).customCards.push({
  type: "git-repo-monitor-card",
  name: "Git Repo Monitor",
  description: "State of your git repositories (local, CI, releases, stars, active installs)",
  preview: true,
  documentationURL: "https://github.com/Elwinmage/ha-git-repo-monitor",
});

console.info(`%c GIT-REPO-MONITOR-CARD %c ${VERSION} `, "color:#fff;background:#24292f", "color:#24292f");

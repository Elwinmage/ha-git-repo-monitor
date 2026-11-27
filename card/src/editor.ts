// Visual editor: the plain options through ha-form, plus the hand-picked, ordered repository list.

import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { translator } from "./i18n";
import { MONITOR_ID, repoKey, type CardConfig, type Hass, type RepoPayload } from "./types";

type Translate = (key: string) => string;

interface RepoChoice {
  key: string;
  name: string;
  host: string;
}

/** Schema of the options handled by ha-form (everything but the repository list). */
export function configSchema(t: Translate): unknown[] {
  const bool = (name: string) => ({ name, selector: { boolean: {} } });
  return [
    { name: "title", selector: { text: {} } },
    { name: "host", selector: { text: {} } },
    {
      name: "sort",
      selector: {
        select: {
          mode: "dropdown",
          options: [
            { value: "status", label: t("editor.sort.status") },
            { value: "name", label: t("editor.sort.name") },
          ],
        },
      },
    },
    {
      type: "grid",
      name: "",
      schema: [bool("show_ok"), bool("expand_problems"), bool("compact")],
    },
    {
      type: "expandable",
      name: "",
      title: t("editor.columns"),
      schema: [bool("show_version"), bool("show_ci"), bool("show_coverage"), bool("show_stars"), bool("show_installs")],
    },
  ];
}

/** The bare repository name of a configuration entry ("alare/ha-reef-card" -> "ha-reef-card"). */
function bareName(entry: string): string {
  const slash = entry.indexOf("/");
  return slash < 0 ? entry : entry.slice(slash + 1);
}

class GitRepoMonitorCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  hass?: Hass;
  private _config: CardConfig = { type: "custom:git-repo-monitor-card" };

  setConfig(config: CardConfig): void {
    this._config = { ...config };
  }

  private get _t(): Translate {
    return translator(this.hass?.locale?.language ?? this.hass?.language ?? "en");
  }

  /** Repositories published right now, by name; limited to the configured host filter. */
  private _choices(): RepoChoice[] {
    const choices: RepoChoice[] = [];
    for (const entity of Object.values(this.hass?.states ?? {})) {
      const data = entity.attributes as RepoPayload | undefined;
      if (data?.monitor !== MONITOR_ID || !data.repo) continue;
      if (this._config.host && data.host !== this._config.host) continue;
      choices.push({ key: repoKey(data), name: data.name || data.repo, host: data.host });
    }
    return choices.sort((a, b) => a.name.localeCompare(b.name));
  }

  private _apply(config: CardConfig): void {
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
  }

  private _setRepos(repos: string[]): void {
    const config: CardConfig = { ...this._config };
    if (repos.length) config.repos = repos;
    else delete config.repos;
    this._apply(config);
  }

  private _formChanged(ev: CustomEvent): void {
    // ha-form only knows the plain options: keep the repository list as it is.
    this._apply({ ...this._config, ...(ev.detail.value as CardConfig) });
  }

  private _add(key: string): void {
    this._setRepos([...(this._config.repos ?? []), key]);
  }

  private _remove(index: number): void {
    const repos = [...(this._config.repos ?? [])];
    repos.splice(index, 1);
    this._setRepos(repos);
  }

  private _move(index: number, delta: number): void {
    const repos = [...(this._config.repos ?? [])];
    const target = index + delta;
    if (target < 0 || target >= repos.length) return;
    [repos[index], repos[target]] = [repos[target], repos[index]];
    this._setRepos(repos);
  }

  protected render(): TemplateResult {
    const t = this._t;
    const selection = this._config.repos ?? [];
    const choices = this._choices();
    const hosts = new Set(choices.map((choice) => choice.host));
    const known = (entry: string) =>
      choices.find((choice) => choice.key === entry || bareName(choice.key) === bareName(entry));
    const available = choices.filter(
      (choice) => !selection.some((entry) => entry === choice.key || bareName(entry) === bareName(choice.key)),
    );

    return html`
      <div class="editor">
        <ha-form
          .hass=${this.hass}
          .data=${this._config}
          .schema=${configSchema(t)}
          .computeLabel=${(schema: { name: string }) => t(`editor.${schema.name}`)}
          @value-changed=${this._formChanged}
        ></ha-form>

        <div class="repos">
          <div class="head">${t("editor.repos")}</div>
          <div class="hint">${t("editor.repos_hint")}</div>

          ${selection.length
            ? html`
                <div class="list">
                  ${selection.map((entry, index) => {
                    const choice = known(entry);
                    return html`
                      <div class="item">
                        <span class="rank">${index + 1}</span>
                        <span class="name">${choice?.name ?? entry}</span>
                        ${choice && hosts.size > 1 ? html`<span class="host">${choice.host}</span>` : nothing}
                        ${choice ? nothing : html`<span class="missing">${t("editor.repos_missing")}</span>`}
                        <button
                          title=${t("editor.up")}
                          ?disabled=${index === 0}
                          @click=${() => this._move(index, -1)}
                        >
                          <ha-icon icon="mdi:arrow-up"></ha-icon>
                        </button>
                        <button
                          title=${t("editor.down")}
                          ?disabled=${index === selection.length - 1}
                          @click=${() => this._move(index, 1)}
                        >
                          <ha-icon icon="mdi:arrow-down"></ha-icon>
                        </button>
                        <button title=${t("editor.remove")} @click=${() => this._remove(index)}>
                          <ha-icon icon="mdi:close"></ha-icon>
                        </button>
                      </div>
                    `;
                  })}
                </div>
                <button class="link" @click=${() => this._setRepos([])}>${t("editor.repos_clear")}</button>
              `
            : html`<div class="all">${t("editor.repos_all")}</div>`}
          ${available.length
            ? html`
                <div class="sub">${t("editor.repos_available")}</div>
                <div class="chips">
                  ${available.map(
                    (choice) => html`
                      <button class="chip" @click=${() => this._add(choice.key)}>
                        <ha-icon icon="mdi:plus"></ha-icon>${choice.name}
                        ${hosts.size > 1 ? html`<span class="host">${choice.host}</span>` : nothing}
                      </button>
                    `,
                  )}
                </div>
              `
            : nothing}
        </div>
      </div>
    `;
  }

  static styles = css`
    .repos {
      margin-top: 16px;
      border-top: 1px solid var(--divider-color, #e0e0e0);
      padding-top: 12px;
    }
    .head {
      font-weight: 500;
    }
    .hint,
    .all {
      color: var(--secondary-text-color);
      font-size: 0.85em;
      margin: 4px 0 8px;
    }
    .sub {
      color: var(--secondary-text-color);
      font-size: 0.85em;
      margin: 12px 0 4px;
    }
    .item {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 2px 0;
    }
    .rank {
      color: var(--secondary-text-color);
      font-size: 0.85em;
      min-width: 1.2em;
      text-align: right;
    }
    .name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .host {
      color: var(--secondary-text-color);
      font-size: 0.8em;
    }
    .missing {
      color: var(--warning-color, #f90);
      font-size: 0.8em;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    button {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: none;
      border: 1px solid var(--divider-color, #ccc);
      border-radius: 16px;
      color: var(--primary-color);
      cursor: pointer;
      font: inherit;
      font-size: 0.9em;
      padding: 2px 8px;
    }
    button:disabled {
      opacity: 0.35;
      cursor: default;
    }
    button.link {
      border: none;
      padding: 4px 0;
      font-size: 0.85em;
    }
    ha-icon {
      --mdc-icon-size: 16px;
    }
  `;
}

customElements.define("git-repo-monitor-card-editor", GitRepoMonitorCardEditor);

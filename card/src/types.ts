// Shared types and constants between the card and its visual editor.

export const MONITOR_ID = "git_repo_monitor";

export interface Workflow {
  name: string;
  state: string;
  branch?: string | null;
  event?: string | null;
  url?: string | null;
}

export interface RepoPayload {
  monitor: string;
  host: string;
  repo: string;
  name: string;
  path: string;
  full_name: string | null;
  html_url: string | null;
  status: string;
  version: string | null;
  local: {
    branch?: string | null;
    upstream?: string | null;
    ahead?: number;
    behind?: number;
    dirty?: boolean;
    dirty_count?: number;
    tag?: string | null;
    commits_since_tag?: number | null;
    last_commit?: { sha: string; subject: string; date: string } | null;
    coverage?: number | null;
  };
  remote: {
    available?: boolean;
    stars?: number;
    forks?: number;
    open_issues?: number;
    open_prs?: number;
    dependabot_prs?: number;
    latest_release?: { tag: string; published_at: string; url: string } | null;
    unreleased_commits?: number | null;
    workflows?: Workflow[];
  };
  ci: { total: number; ok: number; failed: number; running: number };
  stars: number | null;
  stars_delta: number | null;
  installs: { total: number; versions: Record<string, number> } | null;
  cmd_topic: string;
  updated_at: string;
}

export interface CardConfig {
  type: string;
  title?: string;
  host?: string;
  // Repositories to display, in that order ("<host>/<repo>" or just "<repo>"); empty means all of them.
  repos?: string[];
  sort?: "status" | "name";
  show_ok?: boolean;
  expand_problems?: boolean;
  compact?: boolean;
  show_version?: boolean;
  show_ci?: boolean;
  show_coverage?: boolean;
  show_stars?: boolean;
  show_installs?: boolean;
}

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
}

export interface Hass {
  states: Record<string, HassEntity>;
  locale?: { language: string };
  language?: string;
  callService(domain: string, service: string, data: Record<string, unknown>): Promise<unknown>;
}

/** Stable identifier of a repository inside the card configuration. */
export function repoKey(data: { host: string; repo: string }): string {
  return `${data.host}/${data.repo}`;
}

/** Position of a repository in the configured list, or -1 when it is not listed. */
export function repoRank(data: { host: string; repo: string }, selection: string[]): number {
  const full = selection.indexOf(repoKey(data));
  return full >= 0 ? full : selection.indexOf(data.repo);
}

#!/usr/bin/env bash
# Create (or update) the ha-git-repo-monitor webhook on GitHub repositories.
#
# Usage: setup_webhooks.sh <public url> [owner/repo ...]
#   GRM_WEBHOOK_SECRET must hold the secret configured in the daemon.
#   Without repositories, every non-archived repository of the authenticated user is configured.
# Requires the GitHub CLI (gh) authenticated with the admin:repo_hook scope.
set -euo pipefail

URL="${1:?usage: $0 <public url> [owner/repo ...]}"
shift
: "${GRM_WEBHOOK_SECRET:?GRM_WEBHOOK_SECRET is not set}"

EVENTS=(push workflow_run release create delete star watch fork pull_request issues repository)

if [ "$#" -gt 0 ]; then
  REPOS=("$@")
else
  mapfile -t REPOS < <(gh repo list --source --no-archived --limit 200 --json nameWithOwner -q '.[].nameWithOwner')
fi

event_args=()
for event in "${EVENTS[@]}"; do
  event_args+=(-f "events[]=$event")
done

for repo in "${REPOS[@]}"; do
  hook_id=$(gh api "repos/$repo/hooks" --jq ".[] | select(.config.url == \"$URL\") | .id" 2>/dev/null | head -n 1 || true)
  if [ -n "$hook_id" ]; then
    gh api -X PATCH "repos/$repo/hooks/$hook_id" \
      -f "config[url]=$URL" -f "config[content_type]=json" -f "config[secret]=$GRM_WEBHOOK_SECRET" \
      -F active=true "${event_args[@]}" >/dev/null
    echo "updated  $repo"
  else
    gh api -X POST "repos/$repo/hooks" -f name=web \
      -f "config[url]=$URL" -f "config[content_type]=json" -f "config[secret]=$GRM_WEBHOOK_SECRET" \
      -F active=true "${event_args[@]}" >/dev/null
    echo "created  $repo"
  fi
done

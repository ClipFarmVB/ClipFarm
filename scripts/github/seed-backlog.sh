#!/usr/bin/env bash
# Create GitHub Issues from the card files in backlog/. Each backlog/*.md
# starts with two HTML-comment header lines:
#   <!-- title: CF-48 · Proxy generation in the pipeline -->
#   <!-- labels: devops, feat, P1, epic -->
# followed by the issue body in markdown.
#
# Idempotent-ish: it skips a card if an OPEN issue with the same title already
# exists, so re-running won't create duplicates.
#
# Prereq: gh installed + `gh auth login`, and labels created:
#   bash scripts/github/setup-labels.sh
#   bash scripts/github/seed-backlog.sh
set -euo pipefail

REPO="ClipFarmVB/ClipFarm"
DIR="$(cd "$(dirname "$0")/backlog" && pwd)"

for f in "$DIR"/*.md; do
  [ -e "$f" ] || continue
  title=$(sed -n 's/^<!-- title: \(.*\) -->$/\1/p' "$f" | head -1)
  labels=$(sed -n 's/^<!-- labels: \(.*\) -->$/\1/p' "$f" | head -1 | tr -d ' ')
  body=$(sed '1,2d' "$f")

  if [ -z "$title" ]; then echo "!! $f missing title header — skipping"; continue; fi

  if gh issue list --repo "$REPO" --state open --search "\"$title\" in:title" --json title \
       | grep -qF "$title"; then
    echo "= exists, skipping: $title"
    continue
  fi

  label_args=()
  IFS=',' read -ra L <<< "$labels"
  for l in "${L[@]}"; do [ -n "$l" ] && label_args+=(--label "$l"); done

  echo "+ creating: $title  [$labels]"
  gh issue create --repo "$REPO" --title "$title" --body "$body" "${label_args[@]}"
done

echo "Done. See: gh issue list --repo $REPO"

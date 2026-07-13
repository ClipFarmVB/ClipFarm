#!/usr/bin/env bash
# Create the ClipFarm issue-label taxonomy. Idempotent — safe to re-run
# (--force updates an existing label instead of erroring).
#
# Prereq: gh installed + `gh auth login` done, run from anywhere in the repo.
#   bash scripts/github/setup-labels.sh
set -euo pipefail

REPO="ClipFarmVB/ClipFarm"

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }

echo "Creating category labels…"
label "ball-detection" "1D76DB" "ml/pipeline/ball.py — tracking, contacts, rallies"
label "audio"          "0E8A16" "ml/pipeline/audio.py — energy, cheer scoring"
label "scoring"        "5319E7" "ml/pipeline/score.py — highlight scoring / ranking"
label "dead-time"      "B60205" "dead-time / condense pipeline"
label "eval"           "006B75" "ml/eval/ — model evaluation harness"
label "api"            "FBCA04" "api/app — FastAPI, models, routers"
label "web"            "C5DEF5" "web/ — Next.js frontend"
label "devops"         "5DADE2" "Docker, CI, infra, dependencies, Modal"
label "docs"           "D4C5F9" "Markdown, README, guides"

echo "Creating priority labels…"
label "P0" "B60205" "Do first — blocking or high impact"
label "P1" "D93F0B" "Important, near-term"
label "P2" "FEF2C0" "Nice to have / later"

echo "Creating type + tracking labels…"
label "epic"     "3E4B9E" "Multi-ticket effort; tracks child issues"
label "feat"     "0E8A16" "New capability"
label "fix"      "D73A4A" "Bug / regression fix"
label "perf"     "FBCA04" "Performance"
label "chore"    "CCCCCC" "Maintenance / tooling"

echo "Done. Labels ready on $REPO."

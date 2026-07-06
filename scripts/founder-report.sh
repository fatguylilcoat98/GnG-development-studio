#!/usr/bin/env bash
# Generate reports/FOUNDER_REPORT.md, CURRENT_STATUS.md, NEXT_ACTION.md,
# WAITING_ON_CHRIS.md, RISKS.md from Studio's own state (no server needs to be
# running), then append this Studio repo's OWN git status — read-only git
# commands only (status/log), never push/pull/reset/checkout. studio.py itself
# never calls git; that stays a Studio-module-is-automation-free invariant
# (see test_studio.py TestNoAutomation) — this script is the one place git is
# read, and only to describe THIS repo, never any other project's repo.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 studio.py --founder-report

{
  echo ""
  echo "## Git Status of Studio Repo"
  echo '```'
  git status --porcelain=v1 -b 2>/dev/null || echo "(not a git repo or git unavailable)"
  echo '```'
  echo ""
  echo "### Latest commit"
  git log -1 --format="%H %s (%ci)" 2>/dev/null || echo "(no commits yet)"
} >> reports/FOUNDER_REPORT.md

echo "Founder report + Studio git status written to reports/FOUNDER_REPORT.md"

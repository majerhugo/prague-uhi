#!/usr/bin/env bash
# Uloží zmeny v archive/ ako nový commit do vetvy $DATA_BRANCH (bez force pushu, história ostáva).
set -euo pipefail
: "${DATA_BRANCH:?}"

cd archive
git add -A
if git diff --cached --quiet; then
  echo "Archív sa nezmenil"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Archive $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q origin "HEAD:refs/heads/$DATA_BRANCH"
echo "Archív uložený do vetvy $DATA_BRANCH ($(git rev-parse --short HEAD))"

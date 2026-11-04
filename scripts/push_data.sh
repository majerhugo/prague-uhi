#!/usr/bin/env bash
# Uloží zmeny v archive/ ako nový commit do vetvy $DATA_BRANCH (bez force pushu, história ostáva).
# Push sa pri zlyhaní opakuje dlhšie (asi 8 minút), lebo stratená snímka sa nedá doplniť.
set -euo pipefail
source "$(dirname "$0")/retry.sh"
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
retry "uloženie archívu" 6 15 git push -q origin "HEAD:refs/heads/$DATA_BRANCH"
echo "Archív uložený do vetvy $DATA_BRANCH ($(git rev-parse --short HEAD))"

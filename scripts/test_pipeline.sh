#!/usr/bin/env bash
# Skúška skriptov pipeline na lokálnom "vzdialenom" repozitári - bez GitHubu a bez Netatmo.
# Referencia ČHMÚ sa sťahuje naozaj (treba internet).
#
# Použitie: PYTHON=/cesta/k/python bash scripts/test_pipeline.sh legacy/snapshots
set -euo pipefail
PYTHON=${PYTHON:-python}
SRC=$(cd "$(dirname "$0")/.." && pwd)
LEGACY=$(cd "${1:?priečinok so starými snímkami netatmo-*.json}" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

REMOTE="$T/remote.git"
git init -q --bare "$REMOTE"
export REMOTE PYTHON DATA_BRANCH=data PAGES_BRANCH=pages LEGACY_BRANCH=pages
export MIGRATE_SKIP="netatmo-20261031-1347.json netatmo-20261031-1539.json"
ident=(-c user.name=test -c user.email=test@example.com)

echo "== príprava: starý web (index.html + data/snapshots) vo vetve $PAGES_BRANCH"
old="$T/old"
mkdir -p "$old/data/snapshots"
cp "$SRC/index.html" "$old/"
for f in 1347 1539 1548 1649 1748; do cp "$LEGACY/netatmo-20261031-$f.json" "$old/data/snapshots/"; done
git -C "$old" init -q -b "$PAGES_BRANCH"
git -C "$old" add -A
git -C "$old" "${ident[@]}" commit -q -m old
git -C "$old" push -q "$REMOTE" "$PAGES_BRANCH"

work="$T/work"
mkdir -p "$work/data"
cp -r "$SRC/scripts" "$work/"
cp "$SRC"/*.py "$SRC/index.html" "$work/"
cp "$SRC/data/praha.geojson" "$work/data/"
cd "$work"
git init -q -b main   # aby fungovali `git fetch` a `git archive` ako v checkoute

run_pipeline() {
  bash scripts/pull_data.sh
  bash scripts/migrate_legacy.sh
  "$PYTHON" fetch.py --no-fetch
  bash scripts/push_data.sh
  bash scripts/publish_site.sh
}
data_files() { git --git-dir="$REMOTE" ls-tree -r --name-only "$DATA_BRANCH"; }
pages_files() { git --git-dir="$REMOTE" ls-tree -r --name-only "$PAGES_BRANCH"; }
fail=0
check() {
  if eval "$2"; then echo "  ok: $1"; else echo "  CHYBA: $1"; fail=1; fi
}

echo "== skúšobný beh ako v cloude (zápis do iných vetiev, staré snímky sa len čítajú)"
DATA_BRANCH=data-test PAGES_BRANCH=pages-test run_pipeline
check "skúšobná vetva archívu má prevedené snímky" 'git --git-dir="$REMOTE" ls-tree -r --name-only data-test | grep -qx slots/2026-10-31/15.json'
check "skúšobný web je publikovaný" 'git --git-dir="$REMOTE" ls-tree -r --name-only pages-test | grep -qx data/index.json'
check "zdrojová vetva so starými snímkami ostala nezmenená" 'pages_files | grep -q "data/snapshots/"'
check "ostrá vetva data pritom nevznikla" '! git --git-dir="$REMOTE" rev-parse --verify -q data > /dev/null'

echo "== beh 1 (vetva s archívom ešte neexistuje, vo webe sú staré snímky)"
run_pipeline
check "archív má register" 'data_files | grep -qx registry.json'
check "archív má snímku 15:00 UTC" 'data_files | grep -qx slots/2026-10-31/15.json'
check "archív má snímky 16:00 a 17:00 UTC" 'data_files | grep -qx slots/2026-10-31/16.json && data_files | grep -qx slots/2026-10-31/17.json'
check "ručná snímka 13:00 sa nepreviedla" '! data_files | grep -qx slots/2026-10-31/13.json'
check "archív má referenciu ČHMÚ" 'data_files | grep -q "^ref/"'
check "web má index.html" 'pages_files | grep -qx index.html'
check "web má index.json, register a dni" 'pages_files | grep -qx data/index.json && pages_files | grep -qx data/registry.json && pages_files | grep -q "^data/days/"'
check "web už neobsahuje staré snímky" '! pages_files | grep -q snapshots'
slots_before=$(data_files | grep '^slots/' | sort | tr '\n' ' ')
commits_before=$(git --git-dir="$REMOTE" rev-list --count "$DATA_BRANCH")

echo "== beh 2 (archív už existuje, snímky vo webe už nie sú)"
run_pipeline
check "snímky v archíve sa nezmenili" '[ "$(data_files | grep "^slots/" | sort | tr "\n" " ")" = "$slots_before" ]'
check "archív sa nerozrástol o ďalší register" '[ "$(data_files | grep -c "^registry.json$")" = 1 ]'
check "história archívu pribúda, nie sa prepisuje" '[ "$(git --git-dir="$REMOTE" rev-list --count "$DATA_BRANCH")" -ge "$commits_before" ]'
check "web je stále kompletný" 'pages_files | grep -qx data/index.json'

if [ "$fail" = 0 ]; then echo "VŠETKO V PORIADKU"; else echo "SÚ CHYBY"; fi
exit "$fail"

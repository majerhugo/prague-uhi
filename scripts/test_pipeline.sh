#!/usr/bin/env bash
# Skúška skriptov pipeline na lokálnom "vzdialenom" repozitári - bez GitHubu a bez Netatmo.
# Archív sa naplní vymyslenými stanicami, referencia ČHMÚ sa sťahuje naozaj (treba internet).
#
# Použitie: PYTHON=/cesta/k/python bash scripts/test_pipeline.sh
set -euo pipefail
PYTHON=${PYTHON:-python}
SRC=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

REMOTE="$T/remote.git"
git init -q --bare "$REMOTE"
export REMOTE PYTHON DATA_BRANCH=data PAGES_BRANCH=pages
ident=(-c user.name=test -c user.email=test@example.com)

work="$T/work"
mkdir -p "$work/data"
cp -r "$SRC/scripts" "$work/"
cp "$SRC"/*.py "$SRC/index.html" "$work/"
cp "$SRC/data/praha.geojson" "$work/data/"
cd "$work"
git init -q -b main   # aby fungovali git príkazy v skriptoch ako v checkoute

echo "== príprava: vetva $DATA_BRANCH s archívom troch starších hodín (vymyslené stanice)"
# Relatívna cesta: python na Windows nerozumie "/tmp/..." z Git Bash.
"$PYTHON" - seed_archive <<'EOF'
import sys
import time
from pathlib import Path

import archive

base = Path(sys.argv[1])
registry = archive.Registry(base / "registry.json")
first = int(time.time()) // 3600 * 3600 - 10 * 3600   # staršie než 6 h, referencia nemusí byť úplná
for h in range(3):
    hour = first + h * 3600
    stations = [{"id": f"aa:bb:cc:00:00:{i:02d}", "lat": 50.0 + i * 0.002, "lon": 14.3 + i * 0.002,
                 "alt": 200, "temp": 8.0 + i * 0.1, "ts": hour + 30 * 60 + i * 30} for i in range(30)]
    archive.save_slot(base, registry, stations, archive.iso(archive.utc(hour + 50 * 60)))
EOF
git -C seed_archive init -q -b "$DATA_BRANCH"
git -C seed_archive add -A
git -C seed_archive "${ident[@]}" commit -q -m seed
git -C seed_archive push -q "$REMOTE" "$DATA_BRANCH"

run_pipeline() {
  bash scripts/pull_data.sh
  "$PYTHON" fetch.py --no-fetch
  bash scripts/push_data.sh
  bash scripts/publish_site.sh
}
data_files() { git --git-dir="$REMOTE" ls-tree -r --name-only "$DATA_BRANCH"; }
pages_files() { git --git-dir="$REMOTE" ls-tree -r --name-only "$PAGES_BRANCH"; }
index_slots() {
  git --git-dir="$REMOTE" show "$PAGES_BRANCH:data/index.json" \
    | "$PYTHON" -c "import json,sys; print(len(json.load(sys.stdin)['slots']))"
}
fail=0
check() {
  if eval "$2"; then echo "  ok: $1"; else echo "  CHYBA: $1"; fail=1; fi
}

echo "== beh 1 (vetva s webom ešte neexistuje)"
run_pipeline
check "archív má register a tri snímky" '[ "$(data_files | grep -c "^slots/")" = 3 ] && data_files | grep -qx registry.json'
check "archív má referenciu ČHMÚ" 'data_files | grep -q "^ref/"'
check "web má index.html, index.json a register" 'pages_files | grep -qx index.html && pages_files | grep -qx data/index.json && pages_files | grep -qx data/registry.json'
check "web má denné súbory a hranicu Prahy" 'pages_files | grep -q "^data/days/" && pages_files | grep -qx data/praha.geojson'
check "web zverejnil všetky tri snímky" '[ "$(index_slots)" = 3 ]'
slots_before=$(data_files | grep '^slots/' | sort | tr '\n' ' ')

echo "== beh 2 (archív aj web už existujú)"
run_pipeline
check "snímky v archíve sa nezmenili" '[ "$(data_files | grep "^slots/" | sort | tr "\n" " ")" = "$slots_before" ]'
check "web je stále kompletný" '[ "$(index_slots)" = 3 ]'

echo "== poistky"
pages_before=$(git --git-dir="$REMOTE" rev-parse "$PAGES_BRANCH")
cp data/index.json "$T/index.bak"
echo '{"ver":1,"slots":[]}' > data/index.json
bash scripts/publish_site.sh > /dev/null 2>&1
check "prázdny web neprepíše publikovaný" '[ "$(git --git-dir="$REMOTE" rev-parse "$PAGES_BRANCH")" = "$pages_before" ]'
cp "$T/index.bak" data/index.json
check "nedostupný remote zastaví obnovu archívu po opakovaniach" '! RETRY_SLEEP_CMD=: REMOTE=/neexistuje/repo.git bash scripts/pull_data.sh > /dev/null 2>&1'

echo "== opakovanie pri prechodnej chybe"
. scripts/retry.sh
pokusy="$T/pokusy"
echo 0 > "$pokusy"
flaky() { local n=$(( $(cat "$pokusy") + 1 )); echo "$n" > "$pokusy"; [ "$n" -ge 3 ]; }
check "retry uspeje po dvoch neúspešných pokusoch" 'RETRY_SLEEP_CMD=: retry test 5 1 flaky 2> /dev/null && [ "$(cat "$pokusy")" = 3 ]'
check "retry sa po vyčerpaní pokusov vzdá" '! RETRY_SLEEP_CMD=: retry test 2 1 false 2> /dev/null'

# vzdialený repozitár prvý push odmietne (simulácia výpadku GitHubu) a druhý prijme
bash scripts/pull_data.sh > /dev/null
hookcount="$T/hookcount"
echo 0 > "$hookcount"
cat > "$REMOTE/hooks/pre-receive" <<EOF
#!/bin/sh
c=\$(cat "$hookcount"); c=\$((c + 1)); echo \$c > "$hookcount"
[ "\$c" -ge 2 ]
EOF
chmod +x "$REMOTE/hooks/pre-receive"
echo probe > archive/probe.txt
check "uloženie archívu prežije odmietnutý push" 'RETRY_SLEEP_CMD=: bash scripts/push_data.sh > /dev/null 2>&1 && data_files | grep -qx probe.txt && [ "$(cat "$hookcount")" = 2 ]'
rm -f "$REMOTE/hooks/pre-receive"

if [ "$fail" = 0 ]; then echo "VŠETKO V PORIADKU"; else echo "SÚ CHYBY"; fi
exit "$fail"

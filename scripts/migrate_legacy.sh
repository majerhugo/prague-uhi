#!/usr/bin/env bash
# Jednorazový prevod starých surových snímok z vetvy $LEGACY_BRANCH (data/snapshots/) do archívu.
# Vetva sa len číta, takže skúšobný beh môže migrovať ostré snímky bez toho, aby ich zmenil.
# Opakovateľné: termíny, ktoré archív už má, sa nezmenia. Po prvom ostrom publikovaní nového
# webu už vo vetve gh-pages snímky nie sú a skript nič nerobí.
# MIGRATE_SKIP = názvy snímok, ktoré sa vynechajú (ručné spustenia mimo hodinovej mriežky).
set -euo pipefail
: "${REMOTE:?}"
LEGACY_BRANCH=${LEGACY_BRANCH:-gh-pages}
PYTHON=${PYTHON:-python}

set +e
git ls-remote --exit-code --heads "$REMOTE" "$LEGACY_BRANCH" > /dev/null
rc=$?
set -e
case $rc in
  0) ;;
  2) echo "Vetva $LEGACY_BRANCH neexistuje - nie je čo prevádzať"; exit 0 ;;
  *) echo "Nepodarilo sa overiť vetvu $LEGACY_BRANCH (ls-remote skončil s kódom $rc)" >&2; exit 1 ;;
esac

git fetch -q --depth 1 "$REMOTE" "refs/heads/$LEGACY_BRANCH"
if ! git cat-file -e FETCH_HEAD:data/snapshots 2>/dev/null; then
  echo "Vo vetve $LEGACY_BRANCH nie sú staré snímky - nič sa neprevádza"
  exit 0
fi

rm -rf legacy
mkdir legacy
git archive FETCH_HEAD data/snapshots | tar -x -C legacy
# shellcheck disable=SC2086
"$PYTHON" migrate.py legacy/data/snapshots --skip ${MIGRATE_SKIP:-}

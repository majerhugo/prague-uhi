#!/usr/bin/env bash
# Pripraví priečinok archive/ z vetvy $DATA_BRANCH vzdialeného repozitára $REMOTE.
# Ak vetva ešte neexistuje, začne s prázdnym archívom (vetva vznikne pri prvom uložení).
set -euo pipefail
: "${REMOTE:?}" "${DATA_BRANCH:?}"

rm -rf archive
git init -q -b "$DATA_BRANCH" archive
cd archive
git remote add origin "$REMOTE"

# Kód 2 = vetva neexistuje; iná chyba (sieť) nesmie skončiť prázdnym archívom,
# z ktorého by sa potom prepísal web.
set +e
git ls-remote --exit-code --heads origin "$DATA_BRANCH" > /dev/null
rc=$?
set -e
case $rc in
  0)
    git fetch -q --depth 1 origin "$DATA_BRANCH"
    git reset -q --hard FETCH_HEAD
    echo "Archív obnovený z vetvy $DATA_BRANCH ($(git ls-files | wc -l) súborov)"
    ;;
  2)
    echo "Vetva $DATA_BRANCH ešte neexistuje - začína sa s prázdnym archívom"
    ;;
  *)
    echo "Nepodarilo sa overiť vetvu $DATA_BRANCH (ls-remote skončil s kódom $rc)" >&2
    exit 1
    ;;
esac

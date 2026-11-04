#!/usr/bin/env bash
# Pripraví priečinok archive/ z vetvy $DATA_BRANCH vzdialeného repozitára $REMOTE.
# Ak vetva ešte neexistuje, začne s prázdnym archívom (vetva vznikne pri prvom uložení).
set -euo pipefail
source "$(dirname "$0")/retry.sh"
: "${REMOTE:?}" "${DATA_BRANCH:?}"

rm -rf archive
git init -q -b "$DATA_BRANCH" archive
cd archive
git remote add origin "$REMOTE"

# Kód 2 = vetva neexistuje; iná chyba (sieť) sa opakuje a nakoniec zastaví beh, aby sa archív
# nezačal od nuly a neprepísal sa z neho web.
rc=0
ls_remote_branch origin "$DATA_BRANCH" 4 10 || rc=$?
case $rc in
  0)
    retry "stiahnutie archívu" 4 10 git fetch -q --depth 1 origin "$DATA_BRANCH"
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

#!/usr/bin/env bash
# Pomocné funkcie na opakovanie sieťových príkazov. GitHub občas krátko zlyháva (napríklad incident
# s git operáciami 2026-11-04) a zlyhané uloženie by stálo hodinovú snímku, ktorú Netatmo spätne nedá.
# Použitie: source "$(dirname "$0")/retry.sh"
# V testoch RETRY_SLEEP_CMD=: vynechá čakanie.

# retry POPIS POKUSY PAUZA príkaz... - po každom neúspechu sa pauza (s) zdvojnásobí.
# Príkaz sa nevypisuje, lebo môže obsahovať adresu s tokenom.
retry() {
  local label=$1 attempts=$2 delay=$3 n=1
  shift 3
  until "$@"; do
    if [ "$n" -ge "$attempts" ]; then
      echo "$label: zlyhalo aj po $n pokusoch" >&2
      return 1
    fi
    echo "$label: pokus $n zlyhal, ďalší o $delay s" >&2
    ${RETRY_SLEEP_CMD:-sleep} "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
  done
}

# ls_remote_branch ZDROJ VETVA POKUSY PAUZA - vráti 0 (vetva existuje) alebo 2 (neexistuje).
# Každú inú chybu (sieť) opakuje; po vyčerpaní pokusov vráti kód poslednej chyby.
ls_remote_branch() {
  local remote=$1 branch=$2 attempts=$3 delay=$4 n=1 rc
  while true; do
    rc=0
    git ls-remote --exit-code --heads "$remote" "$branch" > /dev/null || rc=$?
    if [ "$rc" = 0 ] || [ "$rc" = 2 ]; then
      return "$rc"
    fi
    if [ "$n" -ge "$attempts" ]; then
      echo "ls-remote $branch: zlyhalo aj po $n pokusoch (kód $rc)" >&2
      return "$rc"
    fi
    echo "ls-remote $branch: pokus $n zlyhal (kód $rc), ďalší o $delay s" >&2
    ${RETRY_SLEEP_CMD:-sleep} "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
  done
}

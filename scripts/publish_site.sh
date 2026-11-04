#!/usr/bin/env bash
# Zverejní web: index.html a súbory z data/ sa nahrajú ako jediný commit do vetvy $PAGES_BRANCH
# (force push, história webu sa neuchováva - história dát je vo vetve s archívom).
set -euo pipefail
source "$(dirname "$0")/retry.sh"
: "${REMOTE:?}" "${PAGES_BRANCH:?}"

# Prázdny web neprepíše dobrý: bez aspoň jednej snímky v indexe sa nič nepublikuje.
if ! grep -q '"slots":\[[0-9]' data/index.json 2>/dev/null; then
  echo "::warning::V data/index.json nie je žiadna snímka, web sa nepublikuje"
  exit 0
fi

rm -rf _site
mkdir -p _site/data
cp index.html _site/
cp data/praha.geojson data/index.json data/registry.json _site/data/
cp -r data/days _site/data/
touch _site/.nojekyll

cd _site
git init -q -b "$PAGES_BRANCH"
git add -A
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Update map $(date -u +%Y-%m-%dT%H:%MZ)"
# Web sa pri ďalšom behu zostaví znova, preto stačí kratšie opakovanie.
retry "zverejnenie webu" 4 10 git push -q -f "$REMOTE" "HEAD:refs/heads/$PAGES_BRANCH"
echo "Web zverejnený do vetvy $PAGES_BRANCH"

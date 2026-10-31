# Tepelný ostrov Prahy z Netatmo staníc

Mapa intenzity mestského tepelného ostrova (UHI) z verejných Netatmo staníc.

Intenzita UHI = teplota Netatmo stanice − priemer teplôt na staniciach ČHMÚ Praha-Ruzyně a Praha-Kbely
(10-minútové otvorené dáta, https://opendata.chmi.cz/meteorology/climate/now/).

## Inštalácia

```
conda env create -f environment.yml
conda activate netatmo-uhi
```

## Prístup k Netatmo API

1. Na https://dev.netatmo.com → **My Apps** otvor svoju aplikáciu.
2. V časti **Token generator** zvoľ scope `read_station` a klikni na **Generate Token**, potom prístup potvrď.
3. Do `.env` doplň Client ID, Client Secret a **Refresh token**.

Skript si potom sám obnovuje token a najnovší refresh token ukladá do `token.json`.
Ak vygeneruješ na webe nový token, súbor `token.json` zmaž.

## Spustenie

```
python fetch.py            # stiahne snímku Netatmo a aktualizuje mapu
python fetch.py --no-fetch # len prepočíta mapu z uložených snímok
python fetch.py --now      # predbežná mapa z najnovšej snímky (najbližší termín ČHMÚ)
python -m http.server      # potom otvor http://localhost:8000
```

Každé spustenie uloží snímku do `data/snapshots/`. Mapa sa počíta z najnovšej snímky,
pre ktorú už ČHMÚ zverejnil merania (ČHMÚ mešká asi hodinu), takže mapa ukazuje stav
približne spred hodiny. Referencia pre každú stanicu je 10-minútový termín ČHMÚ
najbližší k času jej merania.

Odporúčané spúšťanie: každú hodinu okolo HH:05 UTC (≈ 100 z 500 povolených požiadaviek za hodinu).
ČHMÚ zverejňuje okolo HH:01 dáta do HH−1:50, takže snímka z HH−1:05 je vtedy už spracovateľná.

Sťahujú sa len stanice v hranici hl. m. Prahy (`data/praha.geojson`, OpenStreetMap relácia 435514, ODbL).

Stránku nejde otvoriť dvojklikom (`file://`), lebo prehliadač nedovolí načítať dáta. Treba lokálny server.

## Nasadenie (GitHub Actions + GitHub Pages)

Workflow `.github/workflows/update-map.yml` beží každú hodinu o :05 UTC:
obnoví snímky z vetvy `gh-pages`, spustí `fetch.py --keep-hours 48`, uloží prípadne
vymenený refresh token do Secrets a web nahrá do vetvy `gh-pages` (jediný commit, force push).

Secrets repozitára (Settings → Secrets and variables → Actions):
`NETATMO_CLIENT_ID`, `NETATMO_CLIENT_SECRET`, `NETATMO_REFRESH_TOKEN`, `SECRETS_PAT`.

`SECRETS_PAT` je fine-grained token: vlastník = osobný účet, prístup len k tomuto
repozitáru, oprávnenie *Secrets: Read and write*. Pred vypršaním ho treba obnoviť.

Pozor: keď beží web, nespúšťaj `fetch.py` lokálne s tým istým Netatmo tokenom –
obnova tokenu na jednom mieste môže zneplatniť token na druhom. Na lokálny vývoj
použi samostatnú Netatmo aplikáciu alebo `python fetch.py --no-fetch`.

## Súbory

- `fetch.py` – stiahnutie dát, výpočet intenzity → `data/stations.geojson`
- `index.html` – mapa (Leaflet)
- `data/praha.geojson` – hranica Prahy
- `.github/workflows/update-map.yml` – hodinové spúšťanie a zverejnenie

## Známe zjednodušenia (na neskoršie vylepšenie)

- Bez kontroly kvality Netatmo dát.
- Bez korekcie na nadmorskú výšku (Ruzyně 364 m, Kbely 285 m, centrum ~200 m).

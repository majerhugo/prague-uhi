"""Stiahne verejné Netatmo stanice v Prahe a uloží ich do archívu (pozri archive.py).

Intenzita mestského tepelného ostrova (UHI) = teplota Netatmo stanice − priemer teplôt
na staniciach ČHMÚ Praha-Ruzyně a Praha-Kbely v 10-minútovom termíne najbližšom k času
merania stanice. Počíta sa až na webe, v archíve sú surové teploty.

Dáta ČHMÚ meškajú asi hodinu, preto sa snímka na webe objaví, až keď ČHMÚ zverejní
termíny, ktoré potrebuje.

Použitie:
    python fetch.py            # stiahne snímku Netatmo, doplní referenciu ČHMÚ a prebuduje web
    python fetch.py --no-fetch # bez sťahovania z Netatmo (doplní referenciu a prebuduje web)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import archive

ROOT = Path(__file__).parent
ARCHIVE = ROOT / "archive"   # archív (na GitHube vo vetve `data`)
WEB = ROOT / "data"          # súbory generované pre web
TOKEN_FILE = ROOT / "token.json"
NETATMO_API = "https://api.netatmo.com"
CHMI_DATA = "https://opendata.chmi.cz/meteorology/climate/now/data"

BOUNDARY = WEB / "praha.geojson"  # hranica hl. m. Prahy (OpenStreetMap)
TILE_STEP = 0.03             # veľkosť dlaždice (°) pri sťahovaní z Netatmo
REQUEST_PAUSE = 0.5          # s; limit API je 50 požiadaviek / 10 s
RETRY_WAITS = [2, 5, 10]     # s; pauzy pred opakovaním pri chybe 5xx
MAX_AGE_MIN = 30             # meranie staršie než toto (voči času stiahnutia) sa neuloží

# Referenčné stanice ČHMÚ (WSI identifikátor → názov, poloha, nadm. výška)
CHMI_STATIONS = {
    "0-20000-0-11518": {"name": "Praha-Ruzyně", "lat": 50.100278, "lon": 14.255556, "alt": 364},
    "0-20000-0-11567": {"name": "Praha-Kbely", "lat": 50.123333, "lon": 14.538056, "alt": 284.5},
}
REF_STATIONS = [{"id": wsi, **st} for wsi, st in CHMI_STATIONS.items()]
CHMI_DAYS = 3                # ČHMÚ drží v "now" dnešok a dva predchádzajúce dni


def load_boundary():
    """Vonkajší obvod Prahy ako zoznam (lon, lat)."""
    data = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    return [tuple(p) for p in data["features"][0]["geometry"]["coordinates"][0]]


def inside(ring, lon, lat):
    """Je bod v polygóne? (ray casting)"""
    result = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            result = not result
    return result


def tiles(ring, step):
    """Dlaždice (lat_sw, lon_sw, lat_ne, lon_ne) pokrývajúce polygón.

    Dlaždica sa berie, ak je v polygóne niektorý z 5×5 bodov dlaždice
    alebo niektorý vrchol polygónu leží v dlaždici.
    """
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    lat = min(lats)
    while lat < max(lats):
        lon = min(lons)
        while lon < max(lons):
            t = (lat, lon, min(lat + step, max(lats)), min(lon + step, max(lons)))
            samples = ((t[1] + (t[3] - t[1]) * i / 4, t[0] + (t[2] - t[0]) * j / 4)
                       for i in range(5) for j in range(5))
            if (any(inside(ring, x, y) for x, y in samples)
                    or any(t[0] <= y <= t[2] and t[1] <= x <= t[3] for x, y in ring)):
                yield t
            lon += step
        lat += step


# --- Netatmo API -------------------------------------------------------------

def get_access_token():
    """Získa nový access token pomocou refresh tokenu (ten sa ukladá do token.json)."""
    load_dotenv(ROOT / ".env")
    client_id = os.getenv("NETATMO_CLIENT_ID")
    client_secret = os.getenv("NETATMO_CLIENT_SECRET")
    refresh = None
    if TOKEN_FILE.exists():
        refresh = json.loads(TOKEN_FILE.read_text())["refresh_token"]
    refresh = refresh or os.getenv("NETATMO_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh):
        sys.exit("Chýbajú prihlasovacie údaje – doplň .env (pozri .env.example).")

    r = requests.post(f"{NETATMO_API}/oauth2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Obnova tokenu zlyhala ({r.status_code}): {r.text}")
    tok = r.json()
    # Netatmo môže vrátiť nový refresh token – starý potom prestane platiť.
    TOKEN_FILE.write_text(json.dumps({"refresh_token": tok["refresh_token"]}))
    return tok["access_token"]


def fetch_tile(session, bbox):
    """Stanice v dlaždici; pri dočasných chybách servera (5xx) skúša znova s narastajúcou pauzou."""
    lat_sw, lon_sw, lat_ne, lon_ne = bbox
    for wait in RETRY_WAITS + [None]:
        r = session.get(f"{NETATMO_API}/api/getpublicdata", params={
            "lat_ne": lat_ne, "lon_ne": lon_ne,
            "lat_sw": lat_sw, "lon_sw": lon_sw,
            "required_data": "temperature",
            "filter": "true",
        }, timeout=30)
        if r.status_code == 200:
            return r.json().get("body", [])
        if r.status_code < 500 or wait is None:
            break
        time.sleep(wait)
    print(f"\n  dlaždica vynechaná, chyba {r.status_code}: {r.text[:120]}", file=sys.stderr)
    return None


def parse_station(raw):
    """Vytiahne z odpovede API polohu a posledné meranie teploty."""
    lon, lat = raw["place"]["location"]
    for m in raw.get("measures", {}).values():
        types = m.get("type", [])
        if "temperature" in types and m.get("res"):
            ts, values = max(m["res"].items(), key=lambda kv: int(kv[0]))
            temp = values[types.index("temperature")]
            if temp is None:
                return None
            return {"id": raw["_id"], "lat": lat, "lon": lon,
                    "alt": raw["place"].get("altitude"), "temp": temp, "ts": int(ts)}
    return None


def fetch_netatmo():
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {get_access_token()}"

    ring = load_boundary()
    tile_list = list(tiles(ring, TILE_STEP))
    stations = {}
    failed = 0
    for i, bbox in enumerate(tile_list, 1):
        body = fetch_tile(session, bbox)
        if body is None:
            failed += 1
        for raw in body or []:
            s = parse_station(raw)
            if s:
                stations[s["id"]] = s
        print(f"\r  dlaždica {i}/{len(tile_list)}, staníc: {len(stations)}", end="", flush=True)
        time.sleep(REQUEST_PAUSE)
    print(f"\n  nepodarilo sa stiahnuť {failed} z {len(tile_list)} dlaždíc" if failed else "")
    return [s for s in stations.values() if inside(ring, s["lon"], s["lat"])]


def store_slot(stations):
    """Uloží stiahnuté stanice do archívu (hodinový termín podľa mediánu času meraní)."""
    fetched = datetime.now(timezone.utc)
    fresh = [s for s in stations if fetched.timestamp() - s["ts"] <= MAX_AGE_MIN * 60]
    if not fresh:
        print("  Netatmo nevrátilo žiadne čerstvé merania – snímka sa neukladá.", file=sys.stderr)
        return
    registry = archive.Registry(ARCHIVE / "registry.json")
    rows = len(registry.rows)
    slot, saved = archive.save_slot(ARCHIVE, registry, fresh, archive.iso(fetched))
    print(f"  snímka {archive.utc(slot):%d.%m. %H:00} UTC: {len(fresh)} staníc, "
          f"{len(stations) - len(fresh)} so starým meraním vynechaných, "
          f"nových riadkov v registri: {len(registry.rows) - rows}"
          + ("" if saved else " – termín už mal novšiu snímku, nič sa neuložilo"))


# --- ČHMÚ --------------------------------------------------------------------

def parse_chmi_values(rows):
    """{čas (UTC): °C} z riadkov ČHMÚ [stanica, element, čas, hodnota, ...].

    Chýbajúce meranie ČHMÚ uvádza ako prázdny text (napríklad Ruzyně 2026-11-06 08:50 UTC),
    takéto hodnoty sa preskočia - berú sa len čísla.
    """
    values = {}
    for _, element, dt, val, *_ in rows:
        if element == "T" and isinstance(val, (int, float)) and not isinstance(val, bool):
            values[datetime.fromisoformat(dt.replace("Z", "+00:00"))] = val
    return values


def fetch_chmi_series():
    """{wsi: {čas (UTC): °C}} z 10-min. dát ČHMÚ (element T) za posledné dni."""
    today = datetime.now(timezone.utc).date()
    series = {}
    for wsi in CHMI_STATIONS:
        series[wsi] = values = {}
        for back in range(CHMI_DAYS):
            day = today - timedelta(days=back)
            r = requests.get(f"{CHMI_DATA}/10m-{wsi}-{day:%Y%m%d}.json", timeout=30)
            if r.status_code != 200:
                continue
            values.update(parse_chmi_values(r.json()["data"]["data"]["values"]))
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-fetch", action="store_true",
                        help="nesťahovať Netatmo, len doplniť referenciu a prebudovať web")
    args = parser.parse_args()

    if not args.no_fetch:
        print("Sťahujem dáta z Netatmo…")
        store_slot(fetch_netatmo())

    print("Sťahujem referenciu z ČHMÚ…")
    try:
        changed = archive.RefStore(ARCHIVE).update(fetch_chmi_series())
        print("  doplnené dni: " + (", ".join(f"{d:%d.%m.}" for d in changed) or "žiadne zmeny"))
    except Exception as e:
        # Chyba pri referencii nesmie zastaviť zverejnenie webu - zostaví sa z už uloženej referencie.
        print(f"::warning::Referencia ČHMÚ sa neaktualizovala ({type(e).__name__}: {e}), použije sa už uložená")

    slots = archive.build_web(ARCHIVE, WEB, REF_STATIONS)
    if slots:
        print(f"  web: {len(slots)} snímok, od {archive.utc(slots[0]):%d.%m. %H:00} "
              f"do {archive.utc(slots[-1]):%d.%m. %H:00} UTC")
    else:
        print("  web: zatiaľ žiadna snímka s referenciou")


if __name__ == "__main__":
    main()

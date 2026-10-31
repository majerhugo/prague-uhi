"""Stiahne verejné Netatmo stanice v Prahe a vypočíta intenzitu mestského tepelného ostrova.

Intenzita UHI = teplota Netatmo stanice − priemer teplôt na staniciach ČHMÚ
Praha-Ruzyně a Praha-Kbely v 10-minútovom termíne najbližšom k času merania stanice.

Dáta ČHMÚ meškajú asi hodinu. Každé spustenie preto uloží snímku Netatmo staníc
do data/snapshots/ a mapu vypočíta z najnovšej snímky, pre ktorú už ČHMÚ
zverejnil potrebné termíny.

Použitie:
    python fetch.py            # stiahne snímku a aktualizuje mapu
    python fetch.py --no-fetch # len prepočíta mapu z uložených snímok
    python fetch.py --now      # najnovšia snímka + najbližší dostupný termín ČHMÚ (na testovanie)
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "stations.geojson"
SNAPSHOTS = ROOT / "data" / "snapshots"
TOKEN_FILE = ROOT / "token.json"
NETATMO_API = "https://api.netatmo.com"
CHMI_DATA = "https://opendata.chmi.cz/meteorology/climate/now/data"

BOUNDARY = ROOT / "data" / "praha.geojson"  # hranica hl. m. Prahy (OpenStreetMap)
TILE_STEP = 0.03             # veľkosť dlaždice (°) pri sťahovaní z Netatmo
REQUEST_PAUSE = 0.5          # s; limit API je 50 požiadaviek / 10 s
RETRY_WAITS = [2, 5, 10]     # s; pauzy pred opakovaním pri chybe 5xx
MAX_AGE_MIN = 30             # meranie staršie než toto (voči času stiahnutia) sa neuloží

# Referenčné stanice ČHMÚ (WSI identifikátor → názov, poloha, nadm. výška)
CHMI_STATIONS = {
    "0-20000-0-11518": {"name": "Praha-Ruzyně", "lat": 50.100278, "lon": 14.255556, "alt": 364},
    "0-20000-0-11567": {"name": "Praha-Kbely", "lat": 50.123333, "lon": 14.538056, "alt": 284.5},
}
CHMI_STEP = timedelta(minutes=10)


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


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


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


def save_snapshot(stations):
    fetched = datetime.now(timezone.utc)
    fresh = [s for s in stations if fetched.timestamp() - s["ts"] <= MAX_AGE_MIN * 60]
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"netatmo-{fetched:%Y%m%d-%H%M}.json"
    path.write_text(json.dumps({"fetched": iso(fetched), "stations": fresh}), encoding="utf-8")
    print(f"  snímka: {path.name} ({len(fresh)} staníc, {len(stations) - len(fresh)} so starým meraním vynechaných)")


def load_snapshots():
    """Uložené snímky od najnovšej."""
    for path in sorted(SNAPSHOTS.glob("netatmo-*.json"), reverse=True):
        yield json.loads(path.read_text(encoding="utf-8"))


def prune_snapshots(keep_hours):
    """Zmaže snímky staršie než keep_hours (podľa času v názve súboru)."""
    limit = datetime.now(timezone.utc) - timedelta(hours=keep_hours)
    removed = 0
    for path in SNAPSHOTS.glob("netatmo-*.json"):
        taken = datetime.strptime(path.stem, "netatmo-%Y%m%d-%H%M").replace(tzinfo=timezone.utc)
        if taken < limit:
            path.unlink()
            removed += 1
    if removed:
        print(f"  zmazaných starých snímok: {removed}")


# --- ČHMÚ --------------------------------------------------------------------

def fetch_chmi_temperatures(wsi):
    """10-min. teploty (element T) stanice za včerajšok a dnešok: {čas: °C}."""
    out = {}
    today = datetime.now(timezone.utc).date()
    for day in (today - timedelta(days=1), today):
        r = requests.get(f"{CHMI_DATA}/10m-{wsi}-{day:%Y%m%d}.json", timeout=30)
        if r.status_code != 200:
            continue
        for station, element, dt, val, *_ in r.json()["data"]["data"]["values"]:
            if element == "T" and val is not None:
                out[datetime.fromisoformat(dt.replace("Z", "+00:00"))] = val
    return out


class Reference:
    """Priemerná teplota referenčných staníc v čase.

    Používajú sa len termíny, ktoré majú všetky stanice, aby priemer
    nekolísal podľa toho, ktorá stanica práve chýba.
    """

    def __init__(self):
        self.series = {wsi: fetch_chmi_temperatures(wsi) for wsi in CHMI_STATIONS}
        common = set.intersection(*(set(s) for s in self.series.values()))
        if not common:
            sys.exit("ČHMÚ: žiadny spoločný termín merania referenčných staníc.")
        self.times = sorted(common)
        self.mean = {t: statistics.mean(s[t] for s in self.series.values()) for t in self.times}

    @property
    def last(self):
        return self.times[-1]

    def nearest_available(self, t):
        """Najbližší zverejnený termín bez ohľadu na rozdiel (pre --now)."""
        return min(self.times, key=lambda c: abs(c - t))


def nearest_term(t):
    """10-min. termín ČHMÚ najbližší k času t (13:04 → 13:00, 13:06 → 13:10)."""
    term = t.replace(minute=t.minute // 10 * 10, second=0, microsecond=0)
    return term + CHMI_STEP if t - term >= CHMI_STEP / 2 else term


# --- Výpočet a výstup ----------------------------------------------------------

def find_ready_snapshot(ref):
    """Najnovšia snímka, pre ktorej všetky merania už ČHMÚ zverejnil najbližší termín."""
    for snap in load_snapshots():
        if snap["stations"] and max(nearest_term(utc(s["ts"])) for s in snap["stations"]) <= ref.last:
            return snap
    return None


def write_geojson(snap, ref, approximate):
    features = []
    used_terms = []
    for s in snap["stations"]:
        t = utc(s["ts"])
        term = ref.nearest_available(t) if approximate else nearest_term(t)
        if term not in ref.mean:
            continue
        r = ref.mean[term]
        used_terms.append(term)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(s["lon"], 5), round(s["lat"], 5)]},
            "properties": {
                "id": s["id"],
                "role": "netatmo",
                "temp": s["temp"],
                "ref": round(r, 2),
                "ref_time": iso(term),
                "uhi": round(s["temp"] - r, 1),
                "alt": s["alt"],
                "time": iso(t),
            },
        })

    times = [utc(s["ts"]) for s in snap["stations"]]
    snap_time = utc(statistics.median(s["ts"] for s in snap["stations"]))
    ref_term = min(ref.times, key=lambda c: abs(c - snap_time))
    for wsi, st in CHMI_STATIONS.items():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
            "properties": {"id": wsi, "name": st["name"], "role": "reference",
                           "temp": ref.series[wsi][ref_term], "alt": st["alt"], "time": iso(ref_term)},
        })

    n = sum(f["properties"]["role"] == "netatmo" for f in features)
    data = {
        "type": "FeatureCollection",
        "metadata": {
            "generated": iso(datetime.now(timezone.utc)),
            "approximate": approximate,
            "snapshot_time": iso(snap_time),
            "time_range": [iso(min(times)), iso(max(times))],
            "ref_stations": [st["name"] for st in CHMI_STATIONS.values()],
            "ref_temp": round(ref.mean[ref_term], 2),
            "ref_time": iso(ref_term),
            "ref_terms": [iso(min(used_terms)), iso(max(used_terms))],
            "n_netatmo": n,
        },
        "features": features,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"  mapa: stav k {snap_time:%d.%m. %H:%M} UTC, {n} staníc, "
          f"referencia {ref.mean[ref_term]:.1f} °C ({ref_term:%H:%M} UTC)"
          + (" – PRIBLIŽNE, najbližší termín ČHMÚ" if approximate else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-fetch", action="store_true", help="nesťahovať Netatmo, len prepočítať mapu")
    parser.add_argument("--now", action="store_true",
                        help="použiť najnovšiu snímku s najbližším termínom ČHMÚ (bez čakania)")
    parser.add_argument("--keep-hours", type=float,
                        help="zmazať snímky staršie než toľko hodín (bez zadania sa nemaže nič)")
    args = parser.parse_args()

    if not args.no_fetch:
        print("Sťahujem dáta z Netatmo…")
        save_snapshot(fetch_netatmo())

    print("Sťahujem referenciu z ČHMÚ…")
    ref = Reference()
    print(f"  posledný termín ČHMÚ: {ref.last:%d.%m. %H:%M} UTC")

    if args.now:
        snap = next(load_snapshots(), None)
    else:
        snap = find_ready_snapshot(ref)
    if snap is None:
        print("  zatiaľ žiadna snímka, pre ktorú by ČHMÚ už mal dáta – mapa sa nemení.")
    else:
        write_geojson(snap, ref, args.now)

    if args.keep_hours is not None:
        prune_snapshots(args.keep_hours)


if __name__ == "__main__":
    main()

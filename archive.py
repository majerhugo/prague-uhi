"""Archív histórie: register staníc, hodinové snímky Netatmo a referencia ČHMÚ.

Rozloženie adresára archive/ (na GitHube vo vetve `data`, súbory sa len pridávajú):
    registry.json              register staníc, riadky [mac, lat, lon, alt]
    slots/RRRR-MM-DD/HH.json   jedna snímka Netatmo na hodinový termín (UTC)
    ref/RRRR-MM-DD.json        10-min. teploty referenčných staníc ČHMÚ za deň (UTC)

Archív obsahuje surové teploty bez filtrovania. Intenzita UHI sa počíta až na webe
z teploty stanice a referencie, takže sa dá neskôr zmeniť metóda (kontrola kvality,
korekcia na výšku) a história sa prepočíta bez straty dát.

build_web() vyrobí z archívu súbory pre web: index.json, registry.json a days/RRRR-MM-DD.json.
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MOVE_M = 15            # posun stanice (m), od ktorého vznikne nový riadok registra
HOUR = 3600
REF_STEP = 600         # s; termíny ČHMÚ sú po 10 minútach
REF_PER_DAY = 86400 // REF_STEP
REF_GRACE_H = 6        # snímka s neúplnou referenciou sa zverejní až po tolkých hodinách


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def distance_m(lat1, lon1, lat2, lon2):
    """Vzdialenosť dvoch blízkych bodov v metroch."""
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def nearest_term(ts):
    """10-min. termín ČHMÚ najbližší k času ts (epoch s); polovica sa zaokrúhľuje nahor."""
    return (ts + REF_STEP // 2) // REF_STEP * REF_STEP


def day_start(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


# --- Register staníc -----------------------------------------------------------

class Registry:
    """Register staníc, ktorý sa len dopĺňa. Poradové číslo riadku je jeho trvalý identifikátor.

    Jedna stanica (MAC adresa) môže mať viac riadkov: ak sa presunie o viac než MOVE_M metrov,
    pribudne nový riadok a staršie snímky ďalej odkazujú na pôvodný, takže história ostáva správna.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.rows = []     # [mac, lat, lon, alt]
        self.latest = {}   # mac -> index jeho najnovšieho riadku
        if self.path.exists():
            self.rows = read_json(self.path)["rows"]
            for i, row in enumerate(self.rows):
                self.latest[row[0]] = i

    def index(self, mac, lat, lon, alt):
        """Číslo riadku stanice na danej polohe; v prípade potreby pridá nový."""
        lat, lon = round(lat, 5), round(lon, 5)
        i = self.latest.get(mac)
        if i is not None:
            _, old_lat, old_lon, _ = self.rows[i]
            if distance_m(old_lat, old_lon, lat, lon) <= MOVE_M:
                return i
        self.rows.append([mac, lat, lon, alt])
        self.latest[mac] = len(self.rows) - 1
        return len(self.rows) - 1

    def save(self):
        write_json(self.path, {"ver": 1, "rows": self.rows})


# --- Snímky Netatmo --------------------------------------------------------------

def slot_time(stations):
    """Hodinový termín snímky: hodina (UTC), do ktorej padne medián času meraní."""
    median = int(statistics.median(s["ts"] for s in stations))
    return median - median % HOUR


def slot_path(base, slot):
    d = utc(slot)
    return Path(base) / "slots" / f"{d:%Y-%m-%d}" / f"{d:%H}.json"


def save_slot(base, registry, stations, fetched):
    """Uloží snímku do jej hodinového termínu.

    Vráti (termín, uložené?). Ak termín už existuje, ostáva neskôr stiahnutá snímka
    (`fetched` je ISO čas v UTC, preto sa dá porovnávať ako text).
    """
    slot = slot_time(stations)
    path = slot_path(base, slot)
    if path.exists() and read_json(path)["fetched"] >= fetched:
        return slot, False

    items = sorted(((registry.index(s["id"], s["lat"], s["lon"], s["alt"]), s) for s in stations),
                   key=lambda item: item[0])
    first = min(s["ts"] for s in stations)
    registry.save()  # register sa zapíše pred snímkou, ktorá naň odkazuje
    write_json(path, {
        "ver": 1, "slot": slot, "fetched": fetched, "base": first,
        "i": [i for i, _ in items],                       # číslo riadku v registri
        "t": [round(s["temp"] * 10) for _, s in items],   # teplota v 0,1 °C
        "o": [s["ts"] - first for _, s in items],         # čas merania ako posun od `base` (s)
    })
    return slot, True


# --- Referencia ČHMÚ ---------------------------------------------------------------

class RefStore:
    """Denné súbory s 10-min. teplotami referenčných staníc (v 0,1 °C, None = chýba)."""

    def __init__(self, base):
        self.dir = Path(base) / "ref"
        self.cache = {}

    def _path(self, day):
        return self.dir / f"{day:%Y-%m-%d}.json"

    def day(self, day):
        if day not in self.cache:
            path = self._path(day)
            self.cache[day] = read_json(path) if path.exists() else None
        return self.cache[day]

    def get(self, wsi, term):
        """Teplota stanice v termíne `term` (epoch s) v 0,1 °C alebo None."""
        day = utc(term).date()
        data = self.day(day)
        values = data["s"].get(wsi) if data else None
        return values[(term - day_start(day)) // REF_STEP] if values else None

    def update(self, series):
        """Doplní denné súbory z {wsi: {čas (UTC): °C}}. Vráti zoznam zmenených dní."""
        by_day = defaultdict(lambda: defaultdict(dict))
        for wsi, values in series.items():
            for dt, temp in values.items():
                if not isinstance(temp, (int, float)):
                    continue  # chýbajúce meranie (napríklad prázdny text z ČHMÚ)
                idx = (int(dt.timestamp()) - day_start(dt.date())) // REF_STEP
                by_day[dt.date()][wsi][idx] = round(temp * 10)

        changed = []
        for day, stations in sorted(by_day.items()):
            data = self.day(day) or {"ver": 1, "day": f"{day:%Y-%m-%d}", "step": REF_STEP, "s": {}}
            before = json.dumps(data, sort_keys=True)
            for wsi, values in stations.items():
                row = data["s"].setdefault(wsi, [None] * REF_PER_DAY)
                for idx, value in values.items():
                    row[idx] = value
            if json.dumps(data, sort_keys=True) != before:
                write_json(self._path(day), data)
                self.cache[day] = data
                changed.append(day)
        return changed


# --- Súbory pre web ----------------------------------------------------------------

def web_slot(slot, refs, ref_ids, now):
    """Snímka pre web: pridá referenčné teploty pre 10-min. termíny, ktoré snímka používa.

    Vráti None, kým ČHMÚ nezverejní všetky potrebné termíny (a snímka nie je dosť stará).
    """
    first = nearest_term(slot["base"] + min(slot["o"]))
    last = nearest_term(slot["base"] + max(slot["o"]))
    ref = [[refs.get(wsi, t) for t in range(first, last + 1, REF_STEP)] for wsi in ref_ids]
    complete = all(v is not None for row in ref for v in row)
    if not complete and now - slot["slot"] < REF_GRACE_H * HOUR:
        return None
    return {"slot": slot["slot"], "base": slot["base"], "i": slot["i"], "t": slot["t"],
            "o": slot["o"], "r0": first, "r": ref}


def build_web(base, out, ref_stations, now=None):
    """Z archívu vyrobí index.json, registry.json a days/RRRR-MM-DD.json. Vráti termíny snímok."""
    base, out = Path(base), Path(out)
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    registry_file = base / "registry.json"
    registry = read_json(registry_file) if registry_file.exists() else {"ver": 1, "rows": []}
    refs = RefStore(base)
    ref_ids = [s["id"] for s in ref_stations]

    days = defaultdict(list)
    for path in sorted((base / "slots").glob("*/*.json")):
        slot = web_slot(read_json(path), refs, ref_ids, now)
        if slot:
            days[f"{utc(slot['slot']):%Y-%m-%d}"].append(slot)

    days_dir = out / "days"
    for old in days_dir.glob("*.json"):
        old.unlink()
    for day, slots in days.items():
        write_json(days_dir / f"{day}.json", {"ver": 1, "slots": slots})

    slots = sorted(s["slot"] for day in days.values() for s in day)
    write_json(out / "registry.json", registry)
    write_json(out / "index.json", {"ver": 1, "generated": iso(utc(now)), "step": HOUR,
                                    "ref_stations": ref_stations, "slots": slots})
    return slots

"""Kontrola kvality Netatmo dát metódou CrowdQC+ (Fenner a kol., 2021) nad archívom.

Pre uzavretý UTC deň vyexportuje 30-dňové okno do CSV, spustí originálny R balík CrowdQCplus (qc.R)
a príznaky zapíše do archive/qc/RRRR-MM-DD.json. Archív ostáva surový, príznaky sa len pridávajú.
Súbor dňa má tvar: {"ver","day","window_days","min_hours","package","checked","unchecked",
"f": {"<hodina snímky, epoch s>": [[riadok registra, "m1".."m5"], ...]}}; dôvod = prvá úroveň,
ktorá je FALSE (príznaky v balíku sú kumulatívne).

Premenná prostredia RSCRIPT môže určiť príkaz R, napr. "conda run -n netatmo-uhi Rscript".
"""
import argparse
import csv
import os
import shlex
import shutil
import statistics
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import archive

ARCHIVE = Path("archive")
WINDOW_DAYS = 30
MIN_HOURS = 24            # stanica s menej platnými hodinami v okne sa nekontroluje (ostáva zobrazená)
MIN_STATIONS = 100        # pod touto sieťou sú m2/m4 nespoľahlivé (varuje aj balík), deň sa nezapíše
PACKAGE_VERSION = "1.1.2"
LEVELS = ["m1", "m2", "m3", "m4", "m5"]
R_SCRIPT = Path(__file__).with_name("qc.R")
R_TIMEOUT = 1500          # s


def qc_path(base, day):
    return Path(base) / "qc" / f"{day:%Y-%m-%d}.json"


def find_rscript():
    """Príkaz na spustenie Rscript ako zoznam (RSCRIPT alebo PATH); None, ak R nie je dostupné."""
    env = os.environ.get("RSCRIPT")
    if env:
        return shlex.split(env, posix=os.name != "nt")
    exe = shutil.which("Rscript")
    return [exe] if exe else None


def _day_range(day):
    start = archive.day_start(day)
    return start, start + 86400


def _window(base, day):
    """Okno staníc pre deň: (riadky registra, {riadok: [(termín, °C)]} kontrolované, počet nekontrolovaných)."""
    base = Path(base)
    registry_file = base / "registry.json"
    rows = archive.read_json(registry_file)["rows"] if registry_file.exists() else []
    obs = {}
    for k in range(WINDOW_DAYS):
        d = day - timedelta(days=WINDOW_DAYS - 1 - k)
        for path in sorted((base / "slots" / f"{d:%Y-%m-%d}").glob("*.json")):
            slot = archive.read_json(path)
            for row, t in zip(slot["i"], slot["t"]):
                obs.setdefault(row, []).append((slot["slot"], t / 10))
    checked = {r: v for r, v in obs.items() if len(v) >= MIN_HOURS}
    return rows, checked, len(obs) - len(checked)


def _export(base, day, csv_path):
    rows, checked, unchecked = _window(base, day)
    alts = [r[3] for r in rows if r[3] is not None]
    default_alt = statistics.median(alts) if alts else 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["p_id", "time", "ta", "lon", "lat", "z"])
        for row in sorted(checked):
            _, lat, lon, alt = rows[row]
            for slot, temp in checked[row]:
                w.writerow([row, slot, temp, lon, lat, default_alt if alt is None else alt])
    return len(checked), unchecked


def export_window(base, day, csv_path):
    """Zapíše okno do CSV pre R; vráti počet exportovaných staníc."""
    return _export(base, day, csv_path)[0]


def import_flags(base, day, flags_csv, checked, unchecked):
    """Z CSV s príznakmi z R zapíše súbor dňa a vráti jeho obsah."""
    lo, hi = _day_range(day)
    failed = {}
    with open(flags_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["ta"] in ("", "NA"):
                continue                      # doplnený riadok bez merania
            slot = int(float(r["time"]))
            if not lo <= slot < hi:
                continue
            level = next((m for m in LEVELS if r[m] == "FALSE"), None)
            if level:
                failed.setdefault(slot, []).append([int(r["p_id"]), level])
    data = {"ver": 1, "day": f"{day:%Y-%m-%d}", "window_days": WINDOW_DAYS, "min_hours": MIN_HOURS,
            "package": PACKAGE_VERSION, "checked": checked, "unchecked": unchecked,
            "f": {str(s): sorted(v) for s, v in sorted(failed.items())}}
    archive.write_json(qc_path(base, day), data)
    return data


def run_day(base, day, rscript=None):
    """Spočíta a zapíše kontrolu pre jeden deň. Pri chybe (aj chýbajúcom R) nič nezapíše a vráti False."""
    rscript = rscript or find_rscript()
    if not rscript:
        print("::warning::Rscript nie je dostupný, kontrola kvality sa nespustila")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        checked, unchecked = _export(base, day, src)
        if checked < MIN_STATIONS:
            print(f"  {day:%Y-%m-%d}: málo staníc na kontrolu ({checked} < {MIN_STATIONS}), preskočené")
            return False
        try:
            res = subprocess.run([*rscript, str(R_SCRIPT), str(src), str(out)],
                                 capture_output=True, text=True, timeout=R_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"::warning::R sa nepodarilo spustiť ({type(e).__name__}: {e})")
            return False
        if res.returncode != 0 or not out.exists():
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-5:]
            print(f"::warning::Kontrola kvality pre {day:%Y-%m-%d} zlyhala: " + " | ".join(tail))
            return False
        data = import_flags(base, day, out, checked, unchecked)
    n = sum(len(v) for v in data["f"].values())
    print(f"  {day:%Y-%m-%d}: {checked} staníc, označených hodnôt {n}, nekontrolovaných staníc {unchecked}")
    return True


def _days_with_slots(base, now=None):
    """UTC dni, ktoré majú aspoň jednu snímku a sú uzavreté (pred dnešným UTC dňom)."""
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    today = archive.utc(now).date()
    days = []
    for d in (Path(base) / "slots").glob("*"):
        if d.is_dir() and any(d.glob("*.json")):
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if day < today:
                days.append(day)
    return sorted(days)


def closed_days(base, now=None):
    """Uzavreté UTC dni so snímkou, pre ktoré ešte nie je súbor kontroly."""
    return [d for d in _days_with_slots(base, now) if not qc_path(base, d).exists()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", default=str(ARCHIVE), help="adresár archívu (predvolene archive)")
    parser.add_argument("--pending", action="store_true",
                        help="vypíše pending=true|false (aj do $GITHUB_OUTPUT), či je čo počítať; R nepotrebuje")
    parser.add_argument("--day", help="spočíta jeden deň (RRRR-MM-DD) aj vtedy, keď súbor už existuje")
    parser.add_argument("--recompute", action="store_true", help="prepočíta všetky uzavreté dni a prepíše súbory")
    args = parser.parse_args(argv)
    base = Path(args.archive)

    if args.pending:
        line = f"pending={'true' if closed_days(base) else 'false'}"
        print(line)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(line + chr(10))
        return 0

    if args.day:
        days = [date.fromisoformat(args.day)]
    elif args.recompute:
        days = _days_with_slots(base)
    else:
        days = closed_days(base)
    if not days:
        print("Kontrola kvality: nie je čo počítať")
        return 0
    rscript = find_rscript()
    if not rscript:
        print("::warning::Rscript nie je dostupný, kontrola kvality sa nespustila")
        return 2
    print(f"Kontrola kvality: {len(days)} dní")
    failed = [d for d in days if not run_day(base, d, rscript)]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

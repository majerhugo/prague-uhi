"""Testy kontroly kvality: python -m unittest test_qc -v
Testy s R (RIntegrationTest) sa preskočia, ak nie je dostupný Rscript (premenná RSCRIPT alebo PATH)."""
import csv
import json
import math
import random
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import archive
import qc

HOUR = 3600


def ts(day, hour):
    """Epoch (s) UTC pre dátum a hodinu."""
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp())


def station(mac, t, temp=10.0, lat=50.0, lon=14.4, alt=200):
    return {"id": mac, "lat": lat, "lon": lon, "alt": alt, "temp": temp, "ts": t}


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "archive"
        self.work = Path(tmp.name)
        self.reg = archive.Registry(self.base / "registry.json")

    def add_slot(self, slot, stations_temps):
        """Uloží snímku s meraniami v slote `slot` (epoch); stations_temps = {mac: (temp, lat, lon)}."""
        stations = [station(m, slot + 1800, temp=t, lat=la, lon=lo) for m, (t, la, lo) in stations_temps.items()]
        archive.save_slot(self.base, self.reg, stations, "2026-11-01T00:00:00+00:00")


class ExportTest(TempDirTest):
    def test_export_columns_and_window(self):
        day = date(2026, 11, 3)
        for h in range(24 * 40):                       # 40 dní od 1.10.
            slot = ts(date(2026, 10, 1), 0) + h * HOUR
            self.add_slot(slot, {"a": (10.0 + h % 5, 50.0, 14.4), "b": (11.0, 50.01, 14.41)})
        out = self.work / "in.csv"
        n = qc.export_window(self.base, date(2026, 11, 3), out)
        self.assertEqual(n, 2)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0].keys()), ["p_id", "time", "ta", "lon", "lat", "z"])
        times = [int(r["time"]) for r in rows]
        self.assertEqual(min(times), ts(date(2026, 10, 5), 0))      # okno = 30 dní končiacich 3.11.
        self.assertEqual(max(times), ts(day, 23))
        first = [r for r in rows if r["p_id"] == "0"][0]
        self.assertEqual(float(first["ta"]), 10.0 + (ts(date(2026, 10, 5), 0) - ts(date(2026, 10, 1), 0)) // HOUR % 5)

    def test_export_skips_stations_with_few_hours(self):
        day = date(2026, 11, 1)
        for h in range(48):
            slot = ts(date(2026, 10, 31), 0) + h * HOUR
            temps = {"a": (10.0, 50.0, 14.4)}
            if h >= 38:                                 # b má len 10 hodín
                temps["b"] = (11.0, 50.01, 14.41)
            self.add_slot(slot, temps)
        out = self.work / "in.csv"
        self.assertEqual(qc.export_window(self.base, day, out), 1)
        with open(out, newline="", encoding="utf-8") as f:
            self.assertEqual({r["p_id"] for r in csv.DictReader(f)}, {"0"})


class ImportTest(TempDirTest):
    def write_flags(self, rows):
        path = self.work / "flags.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["p_id", "time", "ta", "m1", "m2", "m3", "m4", "m5"])
            w.writerows(rows)
        return path

    def test_first_failed_level_is_reason(self):
        day = date(2026, 11, 1)
        t0 = ts(day, 5)
        path = self.write_flags([
            [3, t0, 12.5, "TRUE", "FALSE", "FALSE", "FALSE", "FALSE"],   # m2
            [4, t0, 12.5, "TRUE", "TRUE", "TRUE", "TRUE", "TRUE"],       # prešla
            [5, t0, "NA", "FALSE", "FALSE", "FALSE", "FALSE", "FALSE"],  # bez merania - ignoruje sa
            [6, t0 + HOUR, 12.5, "TRUE", "TRUE", "TRUE", "TRUE", "FALSE"],  # m5
            [7, ts(date(2026, 10, 31), 5), 12.5, "FALSE", "FALSE", "FALSE", "FALSE", "FALSE"],  # iný deň
        ])
        data = qc.import_flags(self.base, day, path, checked=3, unchecked=1)
        self.assertEqual(data["f"], {str(t0): [[3, "m2"]], str(t0 + HOUR): [[6, "m5"]]})
        self.assertEqual((data["checked"], data["unchecked"], data["day"]), (3, 1, "2026-11-01"))
        self.assertEqual(archive.read_json(qc.qc_path(self.base, day)), data)


class RunDayTest(TempDirTest):
    def test_run_day_without_rscript_returns_false(self):
        day = date(2026, 11, 1)
        self.add_slot(ts(day, 0), {"a": (10.0, 50.0, 14.4)})
        self.assertFalse(qc.run_day(self.base, day, rscript=["/nonexistent/Rscript"]))
        self.assertFalse(qc.qc_path(self.base, day).exists())


LAST_DAY = date(2026, 11, 14)


@unittest.skipUnless(qc.find_rscript(), "Rscript nie je dostupný (nastav RSCRIPT)")
class RIntegrationTest(TempDirTest):
    """Skutočný balík CrowdQCplus nad syntetickou sieťou (14 dní, hodinové snímky)."""

    def build_network(self, n_regular=120, gap_hours=(), newcomer_hours=10):
        rng = random.Random(42)
        first = ts(date(2026, 11, 1), 0)
        offsets = {d: rng.uniform(-2, 2) for d in range(14)}
        regular = [(f"r{k}", 50.0 + 0.012 * (k // 10), 14.3 + 0.025 * (k % 10)) for k in range(n_regular)]
        special = {"indoor": (50.07, 14.40), "radiative": (50.08, 14.45), "dupA": (50.05, 14.5), "dupB": (50.05, 14.5),
                   "mover": (50.02, 14.35), "newcomer": (50.06, 14.33)}
        for h in range(14 * 24):
            if h in gap_hours:
                continue
            slot, d, hod = first + h * HOUR, h // 24, h % 24
            common = 8 + 4 * math.sin(2 * math.pi * (hod - 9) / 24) + offsets[d]
            temps = {}
            for mac, lat, lon in regular:
                dist = math.hypot((lat - 50.075) * 111, (lon - 14.42) * 71)          # km od centra
                temps[mac] = (common + 3 * math.exp(-dist ** 2 / 18) + rng.gauss(0, 0.2), lat, lon)
            temps["indoor"] = (21 + rng.gauss(0, 0.1), *special["indoor"])
            temps["radiative"] = (common + (6 if 12 <= hod < 18 else 0) + rng.gauss(0, 0.2), *special["radiative"])
            for mac in ("dupA", "dupB"):
                temps[mac] = (common + rng.gauss(0, 0.2), *special[mac])
            lat, lon = (50.02, 14.35) if h < 7 * 24 else (50.03, 14.35)              # ~1,1 km posun
            temps["mover"] = (common + rng.gauss(0, 0.2), lat, lon)
            if h >= 14 * 24 - newcomer_hours:
                temps["newcomer"] = (common + rng.gauss(0, 0.2), *special["newcomer"])
            self.add_slot(slot, temps)
        return {mac: [i for i, row in enumerate(self.reg.rows) if row[0] == mac] for mac in
                [m for m, _, _ in regular] + list(special)}

    def flags(self, rows_by_mac):
        self.assertTrue(qc.run_day(self.base, LAST_DAY))
        data = archive.read_json(qc.qc_path(self.base, LAST_DAY))
        by_row = {}
        for slot_flags in data["f"].values():
            for row, level in slot_flags:
                by_row.setdefault(row, []).append(level)
        return data, by_row

    def test_indoor_is_flagged(self):
        rows = self.build_network()
        _, by_row = self.flags(rows)
        self.assertGreaterEqual(len(by_row.get(rows["indoor"][0], [])), 0.8 * 24)

    def test_radiative_is_flagged_in_some_hours(self):
        rows = self.build_network()
        _, by_row = self.flags(rows)
        self.assertGreaterEqual(len(by_row.get(rows["radiative"][0], [])), 1)

    def test_duplicate_coordinates_flagged_m1(self):
        rows = self.build_network()
        _, by_row = self.flags(rows)
        for mac in ("dupA", "dupB"):
            levels = by_row.get(rows[mac][0], [])
            self.assertGreaterEqual(levels.count("m1"), 0.9 * 24)

    def test_most_regular_stations_pass(self):
        rows = self.build_network()
        data, by_row = self.flags(rows)
        regular = [r for mac, rs in rows.items() if mac.startswith("r") and mac != "radiative" for r in rs]
        failed = sum(len(by_row.get(r, [])) for r in regular)
        # CrowdQC+ z princípu odstraňuje chvosty rozdelenia (m2) a izolované hodnoty (m5); väčšina ostáva
        self.assertLess(failed, 0.4 * 24 * len(regular))

    def test_newcomer_is_unchecked(self):
        rows = self.build_network()
        data, by_row = self.flags(rows)
        self.assertNotIn(rows["newcomer"][0], by_row)
        self.assertGreaterEqual(data["unchecked"], 1)

    def test_moved_station_rows_are_separate(self):
        rows = self.build_network()
        self.assertEqual(len(rows["mover"]), 2)
        data, _ = self.flags(rows)
        self.assertEqual(data["checked"], len(self.reg.rows) - data["unchecked"])

    def test_gap_in_window_is_ok(self):
        rows = self.build_network(gap_hours=range(100, 106))
        data, _ = self.flags(rows)
        self.assertEqual(data["day"], "2026-11-14")

    def test_too_few_stations_writes_nothing(self):
        self.build_network(n_regular=10)
        self.assertFalse(qc.run_day(self.base, LAST_DAY))
        self.assertFalse(qc.qc_path(self.base, LAST_DAY).exists())


if __name__ == "__main__":
    unittest.main()

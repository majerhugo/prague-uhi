"""Testy archívu: python -m unittest -v"""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import archive
import fetch

T0 = int(datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc).timestamp())  # 14:00 UTC
WSI = ["A", "B"]
REF_STATIONS = [{"id": w, "name": w, "lat": 50.0, "lon": 14.0, "alt": 300} for w in WSI]


def station(mac, ts, temp=10.0, lat=50.0, lon=14.4, alt=200):
    return {"id": mac, "lat": lat, "lon": lon, "alt": alt, "temp": temp, "ts": ts}


def dt(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "archive"
        self.out = Path(tmp.name) / "web"


class RegistryTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.reg = archive.Registry(self.base / "registry.json")

    def test_same_station_same_row(self):
        a = self.reg.index("m1", 50.0, 14.4, 200)
        self.assertEqual(a, self.reg.index("m1", 50.0, 14.4, 200))
        self.assertEqual(len(self.reg.rows), 1)

    def test_small_shift_keeps_row(self):
        a = self.reg.index("m1", 50.00000, 14.4, 200)
        self.assertEqual(a, self.reg.index("m1", 50.00010, 14.4, 200))  # ~11 m

    def test_move_over_threshold_adds_row_and_keeps_old(self):
        a = self.reg.index("m1", 50.00000, 14.4, 200)
        b = self.reg.index("m1", 50.00020, 14.4, 200)                   # ~22 m
        self.assertNotEqual(a, b)
        self.assertEqual(self.reg.rows[a][1], 50.0)                      # starý riadok ostal
        self.assertEqual(b, self.reg.index("m1", 50.00020, 14.4, 200))  # nová poloha je už známa

    def test_new_mac_new_row(self):
        self.assertNotEqual(self.reg.index("m1", 50.0, 14.4, 200), self.reg.index("m2", 50.0, 14.4, 200))

    def test_roundtrip_keeps_indices(self):
        self.reg.index("m1", 50.0, 14.4, 200)
        self.reg.index("m1", 50.001, 14.4, 200)
        self.reg.save()
        again = archive.Registry(self.base / "registry.json")
        self.assertEqual(again.rows, self.reg.rows)
        self.assertEqual(again.latest, {"m1": 1})


class SlotTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.reg = archive.Registry(self.base / "registry.json")

    def test_slot_is_hour_of_median_observation(self):
        stations = [station("a", T0 + 30 * 60), station("b", T0 + 40 * 60), station("c", T0 + 59 * 60)]
        self.assertEqual(archive.slot_time(stations), T0)
        late = [station("a", T0 + 3600 + 60), station("b", T0 + 3600 + 120), station("c", T0 + 3600 + 180)]
        self.assertEqual(archive.slot_time(late), T0 + 3600)

    def test_encoding_roundtrip(self):
        stations = [station("a", T0 + 2000, temp=10.3), station("b", T0 + 2300, temp=-1.5, lat=50.1)]
        slot, saved = archive.save_slot(self.base, self.reg, stations, "2026-11-01T14:49:00+00:00")
        self.assertTrue(saved)
        data = archive.read_json(archive.slot_path(self.base, slot))
        self.assertEqual(data["base"], T0 + 2000)
        self.assertEqual(data["t"], [103, -15])
        self.assertEqual(data["o"], [0, 300])
        self.assertEqual([self.reg.rows[i][0] for i in data["i"]], ["a", "b"])

    def test_later_fetch_wins(self):
        first = [station("a", T0 + 2000, temp=10.0), station("b", T0 + 2100)]
        second = [station("a", T0 + 2200, temp=12.0), station("b", T0 + 2300)]
        archive.save_slot(self.base, self.reg, first, "2026-11-01T14:49:00+00:00")
        _, saved = archive.save_slot(self.base, self.reg, second, "2026-11-01T14:55:00+00:00")
        self.assertTrue(saved)
        _, saved = archive.save_slot(self.base, self.reg, first, "2026-11-01T14:49:00+00:00")
        self.assertFalse(saved)
        data = archive.read_json(archive.slot_path(self.base, T0))
        self.assertEqual(data["t"][0], 120)

    def test_registry_is_saved_with_slot(self):
        archive.save_slot(self.base, self.reg, [station("a", T0 + 2000), station("b", T0 + 2100)],
                          "2026-11-01T14:49:00+00:00")
        self.assertEqual(len(archive.Registry(self.base / "registry.json").rows), 2)


class RefTest(TempDirTest):
    def test_update_and_get(self):
        refs = archive.RefStore(self.base)
        series = {"A": {dt(T0): 8.1, dt(T0 + 600): 8.2}, "B": {dt(T0): 9.0}}
        changed = refs.update(series)
        self.assertEqual(len(changed), 1)
        self.assertEqual(refs.get("A", T0), 81)
        self.assertEqual(refs.get("A", T0 + 600), 82)
        self.assertEqual(refs.get("B", T0), 90)
        self.assertIsNone(refs.get("B", T0 + 600))
        self.assertIsNone(refs.get("A", T0 + 86400 * 5))

    def test_update_skips_missing_values(self):
        refs = archive.RefStore(self.base)
        refs.update({"A": {dt(T0): 8.1, dt(T0 + 600): ""}})
        self.assertEqual(refs.get("A", T0), 81)
        self.assertIsNone(refs.get("A", T0 + 600))

    def test_update_is_idempotent_and_survives_reload(self):
        series = {"A": {dt(T0): 8.1}}
        archive.RefStore(self.base).update(series)
        reloaded = archive.RefStore(self.base)
        self.assertEqual(reloaded.update(series), [])
        self.assertEqual(reloaded.get("A", T0), 81)


class ChmiParseTest(unittest.TestCase):
    def test_empty_and_missing_values_are_skipped(self):
        rows = [["S", "T", "2026-11-06T08:40:00Z", 7.5, "", 5.0],
                ["S", "T", "2026-11-06T08:50:00Z", "", "", 4.0],    # skutočný prípad: Ruzyně 08:50 UTC
                ["S", "T", "2026-11-06T09:00:00Z", None, "", 4.0],
                ["S", "H", "2026-11-06T08:40:00Z", 80.0, "", 5.0]]  # iný element sa neberie
        self.assertEqual(list(fetch.parse_chmi_values(rows).values()), [7.5])


class ChmiRecentUrlsTest(unittest.TestCase):
    def test_closed_month_is_one_file_and_current_month_daily(self):
        urls = fetch.chmi_recent_urls("W", date(2026, 10, 30), date(2026, 11, 3), date(2026, 11, 7))
        self.assertEqual(len(urls), 4)
        self.assertTrue(urls[0].endswith("/10/10m-W-202610.json"))
        self.assertEqual([u[-13:] for u in urls[1:]], ["20261101.json", "20261102.json", "20261103.json"])


class WebTest(TempDirTest):
    def make(self, offsets, ref_terms):
        """Snímka s meraniami v daných posunoch od T0 a referenciou pre zadané termíny."""
        reg = archive.Registry(self.base / "registry.json")
        stations = [station(f"m{k}", T0 + o, temp=10 + k) for k, o in enumerate(offsets)]
        archive.save_slot(self.base, reg, stations, "2026-11-01T14:49:00+00:00")
        series = {w: {dt(T0 + t): 8.0 for t in ref_terms} for w in WSI}
        archive.RefStore(self.base).update(series)

    def test_complete_reference_is_published(self):
        self.make([2000, 2200], [1800, 2400])  # merania 14:33 a 14:36 -> termíny 14:30 a 14:40
        now = T0 + 3600 + 600
        slots = archive.build_web(self.base, self.out, REF_STATIONS, now=now)
        self.assertEqual(slots, [T0])
        day = json.loads((self.out / "days" / "2026-11-01.json").read_text(encoding="utf-8"))
        slot = day["slots"][0]
        self.assertEqual(slot["r0"], T0 + 1800)
        self.assertEqual(slot["r"], [[80, 80], [80, 80]])
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["slots"], [T0])
        self.assertEqual([s["id"] for s in index["ref_stations"]], WSI)

    def test_incomplete_reference_waits_then_is_published_anyway(self):
        self.make([2000, 2500], [1800])  # chýba termín 14:40 (posun 2400)
        recent = archive.build_web(self.base, self.out, REF_STATIONS, now=T0 + 3600 + 600)
        self.assertEqual(recent, [])
        old = archive.build_web(self.base, self.out, REF_STATIONS, now=T0 + 7 * 3600)
        self.assertEqual(old, [T0])
        day = json.loads((self.out / "days" / "2026-11-01.json").read_text(encoding="utf-8"))
        self.assertEqual(day["slots"][0]["r"], [[80, None], [80, None]])

    def test_one_missing_reference_station_does_not_block_publishing(self):
        self.make([2000, 2200], [])
        archive.RefStore(self.base).update({"A": {dt(T0 + 1800): 8.0, dt(T0 + 2400): 8.0}})  # B chýba
        slots = archive.build_web(self.base, self.out, REF_STATIONS, now=T0 + 3600 + 600)
        self.assertEqual(slots, [T0])
        day = json.loads((self.out / "days" / "2026-11-01.json").read_text(encoding="utf-8"))
        self.assertEqual(day["slots"][0]["r"], [[80, 80], [None, None]])

    def test_city_stations_are_added_to_slots_and_index(self):
        self.make([2000, 2200], [1800, 2400])
        city = [{"id": "C", "name": "C", "lat": 50.0, "lon": 14.0, "alt": 200}]
        archive.RefStore(self.base).update({"C": {dt(T0 + 1800): 7.0}})  # v termíne 2400 chýba, nič neblokuje
        slots = archive.build_web(self.base, self.out, REF_STATIONS, now=T0 + 3600 + 600, city_stations=city)
        self.assertEqual(slots, [T0])
        day = json.loads((self.out / "days" / "2026-11-01.json").read_text(encoding="utf-8"))
        self.assertEqual(day["slots"][0]["c"], [[70, None]])
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["city_stations"], city)

    def test_empty_archive_builds_empty_index(self):
        self.assertEqual(archive.build_web(self.base, self.out, REF_STATIONS, now=T0), [])
        self.assertEqual(json.loads((self.out / "index.json").read_text(encoding="utf-8"))["slots"], [])

    def test_nearest_term_rounds_half_up(self):
        self.assertEqual(archive.nearest_term(T0 + 299), T0)
        self.assertEqual(archive.nearest_term(T0 + 300), T0 + 600)


if __name__ == "__main__":
    unittest.main()

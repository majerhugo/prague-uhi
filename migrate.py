"""Jednorazový prevod starých surových snímok (data/snapshots/*.json) do archívu.

Snímky mimo pravidelnej hodinovej mriežky (ručné spustenia) sa vynechajú cez --skip.
Prevod je opakovateľný: termín, ktorý už má rovnakú alebo novšiu snímku, sa nezmení.

Použitie:
    python migrate.py legacy/snapshots --skip netatmo-20261031-1347.json netatmo-20261031-1539.json
"""
import argparse
import sys
from pathlib import Path

import archive

ARCHIVE = Path(__file__).parent / "archive"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="priečinok so starými snímkami netatmo-*.json")
    parser.add_argument("--skip", nargs="*", default=[], metavar="NAME", help="názvy súborov, ktoré sa vynechajú")
    parser.add_argument("--archive", type=Path, default=ARCHIVE, help="cieľový archív (predvolene archive/)")
    args = parser.parse_args()

    files = sorted(args.source.glob("netatmo-*.json"))
    if not files:
        sys.exit(f"V {args.source} nie sú žiadne snímky netatmo-*.json.")
    registry = archive.Registry(args.archive / "registry.json")
    saved = skipped = replaced = 0
    for path in files:
        if path.name in args.skip:
            print(f"  vynechané: {path.name}")
            skipped += 1
            continue
        snap = archive.read_json(path)
        if not snap["stations"]:
            print(f"  prázdne: {path.name}")
            skipped += 1
            continue
        slot, ok = archive.save_slot(args.archive, registry, snap["stations"], snap["fetched"])
        print(f"  {path.name} -> {archive.utc(slot):%d.%m. %H:00} UTC, {len(snap['stations'])} staníc"
              + ("" if ok else " (termín už má novšiu snímku)"))
        saved += ok
        replaced += not ok
    registry.save()
    print(f"hotovo: uložených {saved}, vynechaných {skipped}, nezmenených {replaced}; "
          f"register má {len(registry.rows)} riadkov")


if __name__ == "__main__":
    main()

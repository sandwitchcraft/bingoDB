#!/usr/bin/env python3
"""Convert the global item registry CSV into items.json at the repo root.

    python3 registry_to_json.py ~/Downloads/ItemDB.csv
    python3 registry_to_json.py            # prompts for the path

The registry is the one list of item identities every region file keys into: a
region says what *bin* an item goes in, the registry says what the item *is*. Keeping
names and search keywords here, once, is what stops two regions from calling the same
thing "Plastic Bottle" and "Plastic Beverage Bottle" under different ids.

CSV columns (header row required, matched by name, order doesn't matter):
    item_id           kebab-case, unique — the key region files use
    display_name      what the app shows
    keywords          comma-separated search terms (optional)
    material_family   one of MATERIALS (case-insensitive)
    notes             editorial only; never written out

After writing, every region file under data/ is checked against the registry and any
key a region uses that the registry doesn't define — or names differently — is listed.
That's a report, not a failure: reconciling regions is a separate, deliberate edit.
"""

import csv
import glob
import json
import os
import re
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(SCRIPT_DIR, "items.json")

REQUIRED_COLUMNS = ("item_id", "display_name", "material_family")
MATERIALS = ("metal", "glass", "styrofoam", "plastic", "cardboard", "paper", "organic", "other")
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def resolve_path(raw):
    path = os.path.expanduser(raw.strip())
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def ask_csv_path(argv):
    if len(argv) > 1:
        return resolve_path(argv[1])
    while True:
        path = resolve_path(input("Path to the registry CSV: "))
        if os.path.isfile(path):
            return path
        print(f"  No file found at {path}")


def split_keywords(cell):
    return [k.strip() for k in cell.split(",") if k.strip()]


def convert(reader):
    """Build the id -> item mapping, failing loudly on anything that would corrupt a key."""
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV is missing column(s): {', '.join(missing)}")

    items = {}
    for line_no, row in enumerate(reader, start=2):
        item_id = (row.get("item_id") or "").strip()
        if not item_id and not any((v or "").strip() for v in row.values()):
            continue
        if not ID_PATTERN.match(item_id):
            raise ValueError(f"line {line_no}: item_id {item_id!r} is not kebab-case")
        if item_id in items:
            raise ValueError(f"line {line_no}: duplicate item_id {item_id!r}")

        display_name = (row.get("display_name") or "").strip()
        if not display_name:
            raise ValueError(f"line {line_no}: {item_id} has no display_name")

        material = (row.get("material_family") or "").strip().lower()
        if material not in MATERIALS:
            raise ValueError(
                f"line {line_no}: {item_id} has material {material!r}; expected one of {', '.join(MATERIALS)}"
            )

        items[item_id] = {
            "display_name": display_name,
            "keywords": split_keywords(row.get("keywords") or ""),
            "material": material,
        }
    return dict(sorted(items.items()))


def report_region_drift(items):
    """List every region key the registry doesn't define, and every name that disagrees."""
    drift = False
    for path in sorted(glob.glob(os.path.join(SCRIPT_DIR, "data", "**", "*.json"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            region_items = json.load(f).get("items", {})
        unknown = sorted(k for k in region_items if k not in items)
        # A registry key normally carries no display_name in the region file (the registry
        # owns it); one that still does, and disagrees, is a leftover worth surfacing.
        renamed = sorted(
            (k, region_items[k]["display_name"], items[k]["display_name"])
            for k in region_items
            if k in items
            and "display_name" in region_items[k]
            and region_items[k]["display_name"] != items[k]["display_name"]
        )
        if not unknown and not renamed:
            continue
        drift = True
        print(f"\n{os.path.relpath(path, SCRIPT_DIR)}")
        for key in unknown:
            print(f"  not in registry: {key}")
        for key, theirs, ours in renamed:
            print(f"  name differs:    {key}: region {theirs!r} vs registry {ours!r}")
    if not drift:
        print("\nEvery region key is in the registry with a matching display_name.")


def main(argv):
    csv_path = ask_csv_path(argv)
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        items = convert(csv.DictReader(f))

    document = {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "materials": list(MATERIALS),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(items)} items to {OUT_PATH}")

    report_region_drift(items)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)
    except (OSError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Interactive wizard that converts a waste-item CSV into a district JSON file.

Input CSV columns (header row is discarded):
    ItemID, Display Name, Bin, Description

Run with no arguments and answer the prompts:
    python3 csv_to_json.py
"""

import csv
import json
import os
import re
import sys
from datetime import date


def convert(rows):
    """Build the id -> item mapping from an iterable of CSV rows."""
    items = {}
    for line_no, row in enumerate(rows, start=2):
        if not row or not any(field.strip() for field in row):
            continue
        if len(row) < 4:
            raise ValueError(
                f"line {line_no}: expected 4 columns, got {len(row)}: {row!r}"
            )

        item_id, display_name, bin_name, description = (f.strip() for f in row[:4])
        if not item_id:
            raise ValueError(f"line {line_no}: missing ItemID")
        if item_id in items:
            raise ValueError(f"line {line_no}: duplicate ItemID {item_id!r}")

        items[item_id] = {
            "display_name": display_name,
            "bin": bin_name,
            "description": description,
        }
    return items


def ask(prompt, required=True):
    """Prompt until a non-empty answer is given (or immediately if optional)."""
    while True:
        answer = input(f"{prompt}: ").strip()
        if answer or not required:
            return answer
        print("  This field is required.")


def ask_csv_path():
    """Ask for the CSV location; relative paths resolve against the cwd."""
    while True:
        raw = ask("Path to the CSV file")
        path = os.path.expanduser(raw)
        if not path.startswith("/"):
            path = os.path.join(os.getcwd(), path)
        if os.path.isfile(path):
            return path
        print(f"  No file found at {path}")


def ask_location_path():
    """Ask for the location path as a comma-separated list of any length."""
    while True:
        raw = ask("Location path (comma separated, e.g. canada, ontario, toronto)")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            return parts
        print("  Enter at least one location segment.")


def confirm(prompt):
    while True:
        answer = input(f"{prompt} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def dump(document):
    """Serialize with 2-space indent, keeping location_path on a single line."""
    blob = json.dumps(document, indent=2, ensure_ascii=False)
    return re.sub(
        r'"location_path": \[\n\s+(.*?)\n\s+\]',
        lambda m: '"location_path": [%s]' % " ".join(m.group(1).split()),
        blob,
        flags=re.DOTALL,
    )


def main():
    csv_path = ask_csv_path()

    document = {
        "district_name": ask("District name"),
        "provider_name": ask("Provider name"),
        "site_url": ask("Site URL"),
        "last_updated": date.today().isoformat(),
        "version": ask("Version"),
        "location_path": ask_location_path(),
    }

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # discard header row
        document["items"] = convert(reader)

    filename = re.sub(r"\s+", "-", document["district_name"].strip().lower()) + ".json"
    out_path = os.path.join(os.getcwd(), filename)

    if os.path.exists(out_path) and not confirm(f"{filename} already exists. Overwrite?"):
        print("Aborted; nothing was written.")
        return 1

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dump(document) + "\n")

    print(f"Wrote {len(document['items'])} items to {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)
    except (OSError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

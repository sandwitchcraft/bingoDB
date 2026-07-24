#!/usr/bin/env python3
"""Convert a Notion waste-item export (a .zip) into a district JSON file.

Give it the path to the exported zip, either as an argument or when prompted:

    python3 csv_to_json.py ~/Downloads/testHalton.zip
    python3 csv_to_json.py            # prompts for the path

Paths may start with "/" (from root), "~" (from home), or a plain name
(from the current directory).

The zip (Notion double-wraps its exports, so nested zips are extracted too)
is expected to contain, once expanded:

    <export>/<District> <hash>.md          <- district metadata table
    <export>/<District>/<items>.csv        <- the item data (used)
    <export>/<District>/<items>_all.csv    <- an alternate column order (ignored)

Metadata is read from the .md table (district_name, provider_name, site_url,
location_path); last_updated and version in that table are ignored. The output
is written under data/<location_path>/<leaf>.json, with last_updated set to
today and version starting at 1.0.0 (patch-bumped from the existing file, if any).

Item CSV columns (header row is discarded):
    ItemID, Display Name, Bin, Description
"""

import csv
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The four metadata fields we take from the .md; last_updated/version are
# deliberately not among them (they are derived, not copied).
MD_FIELDS = ("district_name", "provider_name", "site_url", "location_path")


def resolve_path(raw):
    """Expand ~, keep absolute paths, resolve everything else against the cwd."""
    path = os.path.expanduser(raw.strip())
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return path


def ask(prompt, required=True):
    """Prompt until a non-empty answer is given (or immediately if optional)."""
    while True:
        answer = input(f"{prompt}: ").strip()
        if answer or not required:
            return answer
        print("  This field is required.")


def confirm(prompt):
    while True:
        answer = input(f"{prompt} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_zip_path(argv):
    """Take the zip path from argv[1] if present, otherwise prompt for it."""
    if len(argv) > 1:
        path = resolve_path(argv[1])
        if not zipfile.is_zipfile(path):
            raise ValueError(f"not a zip file: {path}")
        return path
    while True:
        path = resolve_path(ask("Path to the export zip"))
        if zipfile.is_zipfile(path):
            return path
        if not os.path.exists(path):
            print(f"  No file found at {path}")
        else:
            print(f"  Not a zip file: {path}")


def extract_all(zip_path, dest):
    """Extract zip_path into dest, then extract any nested zips in place.

    Notion wraps its export in an outer zip that contains a single inner zip;
    recursing means the caller never has to care how many layers deep it is.
    """
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    while True:
        nested = [
            os.path.join(root, name)
            for root, _, files in os.walk(dest)
            for name in files
            if name.lower().endswith(".zip")
        ]
        if not nested:
            break
        for inner in nested:
            with zipfile.ZipFile(inner) as zf:
                zf.extractall(os.path.dirname(inner))
            os.remove(inner)


def find_files(root, predicate):
    """Return matching files under root, sorted shallowest-first."""
    matches = [
        os.path.join(dirpath, name)
        for dirpath, _, names in os.walk(root)
        for name in names
        if predicate(dirpath, name)
    ]
    matches.sort(key=lambda p: (p[len(root):].count(os.sep), p))
    return matches


def find_metadata_md(root):
    """The metadata .md is the shallowest .md (item notes sit levels deeper)."""
    mds = find_files(root, lambda d, n: n.lower().endswith(".md"))
    if not mds:
        raise ValueError("no .md metadata document found in the export")
    return mds[0]


def find_item_csv(root):
    """Pick the item CSV, preferring the one WITHOUT the `_all` suffix."""
    csvs = find_files(root, lambda d, n: n.lower().endswith(".csv"))
    if not csvs:
        raise ValueError("no CSV file found in the export")
    wanted = [p for p in csvs if not os.path.splitext(p)[0].lower().endswith("_all")]
    if not wanted:
        raise ValueError("only an `_all` CSV was found; the plain export is missing")
    if len(wanted) > 1:
        joined = "\n  ".join(wanted)
        raise ValueError(f"ambiguous: multiple non-`_all` CSVs found:\n  {joined}")
    return wanted[0]


def _clean_cell(cell):
    """Strip a markdown link down to its URL; otherwise return the text as-is."""
    cell = cell.strip()
    link = re.match(r"\[.*?\]\((.*?)\)", cell)
    return link.group(1).strip() if link else cell


def parse_metadata(md_path):
    """Read the `| field | value |` table into the four fields we keep."""
    found = {}
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            cells = [c for c in line.split("|")]
            if len(cells) < 4:  # not a "| a | b |" table row
                continue
            key = cells[1].strip().lower()
            if key in MD_FIELDS:
                found[key] = _clean_cell(cells[2])

    missing = [field for field in MD_FIELDS if not found.get(field)]
    if missing:
        raise ValueError(f"metadata is missing field(s): {', '.join(missing)}")

    location_path = [p.strip() for p in re.split(r"[/,]", found["location_path"]) if p.strip()]
    if not location_path:
        raise ValueError("location_path is empty")
    found["location_path"] = location_path
    return found


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


def default_output_path(location_path):
    """data/<location_path...>/<leaf>.json, anchored at the repo (script) dir."""
    leaf = location_path[-1]
    return os.path.join(SCRIPT_DIR, "data", *location_path, f"{leaf}.json")


def next_version(out_path):
    """1.0.0 for a new file; otherwise the existing file's version, patch +1."""
    if not os.path.exists(out_path):
        return "1.0.0"
    try:
        with open(out_path, encoding="utf-8") as f:
            current = json.load(f).get("version", "")
        major, minor, patch = (int(part) for part in current.split("."))
    except (OSError, ValueError, json.JSONDecodeError):
        print(f"  Warning: could not read version from {out_path}; starting at 1.0.0")
        return "1.0.0"
    return f"{major}.{minor}.{patch + 1}"


def confirm_output_path(default):
    """Show the intended path and let the user accept or replace it."""
    print(f"\nIntended output path:\n  {default}")
    raw = input("Press Enter to accept, or type a different path: ").strip()
    return default if not raw else resolve_path(raw)


def dump(document):
    """Serialize with 2-space indent, keeping location_path on a single line."""
    blob = json.dumps(document, indent=2, ensure_ascii=False)
    return re.sub(
        r'"location_path": \[\n\s+(.*?)\n\s+\]',
        lambda m: '"location_path": [%s]' % " ".join(m.group(1).split()),
        blob,
        flags=re.DOTALL,
    )


def main(argv):
    zip_path = ask_zip_path(argv)

    with tempfile.TemporaryDirectory() as tmp:
        extract_all(zip_path, tmp)
        meta = parse_metadata(find_metadata_md(tmp))
        csv_path = find_item_csv(tmp)
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)  # discard header row
            items = convert(reader)

    out_path = confirm_output_path(default_output_path(meta["location_path"]))

    document = {
        "district_name": meta["district_name"],
        "provider_name": meta["provider_name"],
        "site_url": meta["site_url"],
        "last_updated": date.today().isoformat(),
        "version": next_version(out_path),
        "location_path": meta["location_path"],
        "items": items,
    }

    if os.path.exists(out_path) and not confirm(
        f"{os.path.basename(out_path)} exists; overwrite as v{document['version']}?"
    ):
        print("Aborted; nothing was written.")
        return 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dump(document) + "\n")

    print(f"Wrote {len(items)} items (v{document['version']}) to {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)
    except (OSError, ValueError, zipfile.BadZipFile) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

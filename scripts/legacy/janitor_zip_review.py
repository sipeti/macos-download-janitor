#!/usr/bin/env python3

import csv
import shutil
import sys
from pathlib import Path

ROOT = Path.home() / "Downloads"
CSV_FILE = ROOT / "_Janitor" / "reports" / "zip_crc_verified.csv"
REVIEW = ROOT / "_Janitor" / "review" / "extracted-zips"
APPLY = "--apply" in sys.argv


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def main():
    if not CSV_FILE.is_file():
        print(f"Missing report: {CSV_FILE}")
        sys.exit(1)

    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    selected = []
    for row in rows:
        if row.get("crc_status") != "CRC_VERIFIED":
            continue

        rel = Path(row["zip_path"])
        is_top_level = len(rel.parts) == 1
        is_zip_archive_dir = len(rel.parts) >= 2 and rel.parts[0] == "_ZIP"
        if not (is_top_level or is_zip_archive_dir):
            continue

        src = ROOT / rel
        if not src.is_file():
            continue

        selected.append((row, src, rel))

    total = sum(int(row["zip_size_bytes"]) for row, _, _ in selected)

    print()
    print("JANITOR ZIP REVIEW")
    print("=" * 70)
    print(f"CRC-verified safe review candidates: {len(selected)}")
    print(f"Total size: {human_size(total)}")
    print()

    for row, _, rel in selected:
        dst = REVIEW / rel
        print(f"{row['zip_size']:>10}  {rel}")
        print(f"            -> {dst.relative_to(ROOT)}")

    if not APPLY:
        print("\nDRY RUN ONLY - nothing was modified.")
        print("Run with --apply to move these ZIPs into review.")
        return

    answer = input(
        f"Move {len(selected)} CRC-verified ZIPs ({human_size(total)}) into review? [yes/NO] "
    )
    if answer != "yes":
        print("Aborted.")
        sys.exit(1)

    moved = 0
    moved_bytes = 0
    for row, src, rel in selected:
        dst = REVIEW / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"SKIP exists: {dst}")
            continue
        shutil.move(str(src), str(dst))
        moved += 1
        moved_bytes += int(row["zip_size_bytes"])

    print("=" * 70)
    print(f"Moved: {moved} files")
    print(f"Moved size: {human_size(moved_bytes)}")
    print(f"Review dir: {REVIEW}")
    print("Nothing was deleted.")


if __name__ == "__main__":
    main()

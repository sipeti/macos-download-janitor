#!/usr/bin/env python3

import csv
import hashlib
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path.home() / "Downloads"
INVENTORY = ROOT / "fromGPT_inventory" / "gpt_inventory.csv"
DEST_ROOT = ROOT / "fromGPT"
APPLY = "--apply" in sys.argv


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_destination(path):
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem}__{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def main():
    if not INVENTORY.is_file():
        print(f"Missing inventory: {INVENTORY}")
        sys.exit(1)

    with open(INVENTORY, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    existing = []
    for row in rows:
        src = ROOT / row["path"]
        if src.is_file():
            row["source"] = src
            row["hash"] = sha256(src)
            existing.append(row)

    groups = defaultdict(list)
    for row in existing:
        groups[(int(row["size_bytes"]), row["hash"])].append(row)

    duplicates = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (int(r.get("mtime") or 0), r["path"]))
        for row in group[1:]:
            duplicates.add(row["path"])

    moves = []
    for row in existing:
        src = row["source"]
        if row["path"] in duplicates:
            dst = DEST_ROOT / "_duplicates_review" / src.name
            kind = "duplicate"
        else:
            dst = DEST_ROOT / row["category"] / src.name
            kind = "organized"
        dst = unique_destination(dst)
        moves.append((src, dst, kind, row))

    total = sum(int(row["size_bytes"]) for _, _, _, row in moves)
    dup_count = sum(1 for _, _, kind, _ in moves if kind == "duplicate")

    print()
    print("CHATGPT DOWNLOAD CLEANUP")
    print("=" * 70)
    print(f"Files:      {len(moves)}")
    print(f"Duplicates: {dup_count}")
    print(f"Bytes:      {total}")
    print()

    for src, dst, kind, _ in moves:
        print(f"[{kind}] {src.relative_to(ROOT)}")
        print(f"       -> {dst.relative_to(ROOT)}")

    if not APPLY:
        print("\nDRY RUN ONLY - nothing was moved.")
        print("Run with --apply to perform the moves.")
        return

    answer = input(f"Move {len(moves)} files into {DEST_ROOT}? [yes/NO] ")
    if answer != "yes":
        print("Aborted.")
        sys.exit(1)

    moved = 0
    for src, dst, _, _ in moves:
        if not src.exists():
            print(f"SKIP missing: {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = unique_destination(dst)
        shutil.move(str(src), str(dst))
        moved += 1

    print(f"Moved: {moved}")
    print("Nothing was deleted.")


if __name__ == "__main__":
    main()

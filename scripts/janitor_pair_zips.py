#!/usr/bin/env python3

import csv
import hashlib
import os
import shutil
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path.home() / "Downloads"
JANITOR = ROOT / "_Janitor"
EXTRACT_REPORT = JANITOR / "reports" / "zip_extracted_report.csv"
CRC_REPORT = JANITOR / "reports" / "zip_crc_verified.csv"
OLD_ZIP_REVIEW = JANITOR / "review" / "extracted-zips"
PAIR_ROOT = JANITOR / "review" / "paired"
PLAN_CSV = JANITOR / "reports" / "zip_pair_plan.csv"
APPLY = "--apply" in sys.argv

GENERIC_ROOT_NAMES = {
    "main files", "main file", "documentation", "docs", "doc", "plugins", "plugin",
    "images", "image", "assets", "asset", "files", "file", "resources", "resource",
    "downloads", "download", "data", "content", "contents", "src", "source", "sources",
}


def human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024.0


def safe_name(name):
    for c in '<>:"/\\|?*':
        name = name.replace(c, "_")
    name = name.strip().strip(".")
    return (name or "pair")[:120]


def is_inside(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_generic_root(path):
    return path.name.strip().lower() in GENERIC_ROOT_NAMES


def eligible_zip_relpath(rel):
    parts = rel.parts
    return len(parts) == 1 or (parts and parts[0] == "_ZIP")


def resolve_zip_path(rel):
    original = ROOT / rel
    if original.is_file():
        return original, "original"
    staged = OLD_ZIP_REVIEW / rel
    if staged.is_file():
        return staged, "old_review"
    return None, "missing"


def zip_top_levels(zip_path):
    result = set()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name.startswith("__MACOSX/"):
                    continue
                parts = Path(name).parts
                if not parts:
                    continue
                if parts[-1] == ".DS_Store" or parts[-1].startswith("._"):
                    continue
                result.add(parts[0])
    except Exception:
        return []
    return sorted(result)


def infer_extracted_object(detail, zip_path):
    base_text = detail.get("candidate_base", "").strip()
    if not base_text:
        return None, "NO_CANDIDATE_BASE"

    base = Path(base_text)
    label = detail.get("candidate_label", "").strip()
    common_root = detail.get("common_root", "").strip()
    strip_root = detail.get("strip_root", "").strip()

    if is_inside(base, JANITOR):
        return None, "CANDIDATE_INSIDE_JANITOR"

    if common_root and not strip_root:
        obj = base / common_root
        if obj.exists():
            return obj, "COMMON_ROOT_OBJECT"

    if strip_root:
        dedicated_labels = ("folder_stem", "folder_normalized_stem", "common_root_folder")
        if any(token in label for token in dedicated_labels):
            if base.exists():
                return base, "DEDICATED_FOLDER_STRIPPED_ROOT"
        return None, "STRIPPED_INTO_SHARED_DIRECTORY"

    dedicated_labels = ("folder_stem", "folder_normalized_stem")
    if any(token in label for token in dedicated_labels):
        if base.exists():
            return base, "DEDICATED_FOLDER"

    tops = zip_top_levels(zip_path)
    if len(tops) == 1:
        obj = base / tops[0]
        if obj.exists():
            return obj, "SINGLE_TOP_LEVEL_OBJECT"
    if len(tops) > 1:
        return None, "MULTIPLE_TOP_LEVEL_OBJECTS"
    return None, "NO_EXTRACTED_OBJECT"


def relative_to_root(path):
    try:
        return path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None


def main():
    if not EXTRACT_REPORT.is_file():
        print(f"Missing report: {EXTRACT_REPORT}")
        sys.exit(1)
    if not CRC_REPORT.is_file():
        print(f"Missing report: {CRC_REPORT}")
        sys.exit(1)

    with open(EXTRACT_REPORT, newline="", encoding="utf-8-sig") as f:
        extract_rows = list(csv.DictReader(f))
    extract_by_zip = {row["zip_path"]: row for row in extract_rows}

    with open(CRC_REPORT, newline="", encoding="utf-8-sig") as f:
        crc_rows = list(csv.DictReader(f))

    entries = []
    skipped = []
    location_stats = defaultdict(int)

    for crc_row in crc_rows:
        if crc_row.get("crc_status") != "CRC_VERIFIED":
            skipped.append((crc_row.get("zip_path", "?"), "NOT_CRC_VERIFIED"))
            continue

        rel = Path(crc_row["zip_path"])
        if not eligible_zip_relpath(rel):
            skipped.append((str(rel), "ZIP_INSIDE_OTHER_TREE"))
            continue

        zip_path, location = resolve_zip_path(rel)
        if zip_path is None:
            skipped.append((str(rel), "ZIP_NOT_FOUND"))
            continue
        location_stats[location] += 1

        detail = extract_by_zip.get(str(rel))
        if not detail:
            skipped.append((str(rel), "NO_EXTRACT_REPORT"))
            continue

        extracted, reason = infer_extracted_object(detail, zip_path)
        if extracted is None:
            skipped.append((str(rel), reason))
            continue

        if extracted.is_dir() and is_generic_root(extracted):
            skipped.append((str(rel), "GENERIC_EXTRACTED_DIRECTORY"))
            continue

        extracted_rel = relative_to_root(extracted)
        if extracted_rel is None:
            skipped.append((str(rel), "EXTRACTED_OUTSIDE_DOWNLOADS"))
            continue

        protected = [ROOT, ROOT / "_ZIP", JANITOR, OLD_ZIP_REVIEW, PAIR_ROOT]
        if any(extracted.resolve() == p.resolve() for p in protected):
            skipped.append((str(rel), "PROTECTED_DIRECTORY"))
            continue

        logical_original_zip = ROOT / rel
        if is_inside(logical_original_zip, extracted):
            skipped.append((str(rel), "ZIP_INSIDE_EXTRACTED_TREE"))
            continue

        entries.append({
            "zip_rel": rel,
            "zip_path": zip_path,
            "zip_location": location,
            "crc_row": crc_row,
            "extract_detail": detail,
            "extracted": extracted,
            "extracted_rel": extracted_rel,
            "detect_reason": reason,
        })

    groups = defaultdict(list)
    for entry in entries:
        groups[str(entry["extracted"].resolve())].append(entry)

    group_keys = list(groups.keys())
    overlapping = set()
    for i in range(len(group_keys)):
        a = Path(group_keys[i])
        for j in range(i + 1, len(group_keys)):
            b = Path(group_keys[j])
            if is_inside(a, b) or is_inside(b, a):
                overlapping.add(group_keys[i])
                overlapping.add(group_keys[j])

    for key in list(overlapping):
        group = groups.pop(key, [])
        for entry in group:
            skipped.append((str(entry["zip_rel"]), "OVERLAPPING_EXTRACTED_TREE"))

    bundles = []
    plan_rows = []

    for key, group in groups.items():
        extracted = group[0]["extracted"]
        extracted_rel = group[0]["extracted_rel"]
        digest = hashlib.sha1(str(extracted.resolve()).encode("utf-8")).hexdigest()[:8]
        bundle_name = safe_name(extracted.name) + "__" + digest
        bundle = PAIR_ROOT / bundle_name
        zip_bytes = sum(int(e["crc_row"]["zip_size_bytes"]) for e in group)
        zip_moves = []

        for entry in group:
            dest = bundle / "originals" / entry["zip_rel"]
            zip_moves.append({"source": entry["zip_path"], "destination": dest, "entry": entry})
            plan_rows.append({
                "bundle": bundle_name,
                "type": "zip",
                "source_location": entry["zip_location"],
                "original_relative_path": str(entry["zip_rel"]),
                "source": str(entry["zip_path"]),
                "destination": str(dest),
                "detection": entry["detect_reason"],
            })

        extracted_dest = bundle / "extracted" / extracted_rel
        plan_rows.append({
            "bundle": bundle_name,
            "type": "directory" if extracted.is_dir() else "file",
            "source_location": "downloads",
            "original_relative_path": str(extracted_rel),
            "source": str(extracted),
            "destination": str(extracted_dest),
            "detection": group[0]["detect_reason"],
        })

        bundles.append({
            "name": bundle_name,
            "bundle": bundle,
            "entries": group,
            "zip_moves": zip_moves,
            "zip_bytes": zip_bytes,
            "extracted": extracted,
            "extracted_rel": extracted_rel,
            "extracted_dest": extracted_dest,
        })

    bundles.sort(key=lambda b: b["zip_bytes"], reverse=True)

    PLAN_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = ["bundle", "type", "source_location", "original_relative_path", "source", "destination", "detection"]
    with open(PLAN_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan_rows)

    total_zip_count = sum(len(b["zip_moves"]) for b in bundles)
    total_zip_bytes = sum(b["zip_bytes"] for b in bundles)

    print()
    print("JANITOR ZIP + EXTRACTED PAIR PLAN")
    print("=" * 78)
    print(f"Pair bundles:       {len(bundles)}")
    print(f"ZIP files:          {total_zip_count}")
    print(f"ZIP total size:     {human_size(total_zip_bytes)}")
    print(f"ZIP still original: {location_stats['original']}")
    print(f"ZIP already staged: {location_stats['old_review']}")
    print(f"Skipped:            {len(skipped)}")
    print()

    for bundle in bundles:
        print(f"[PAIR] {bundle['name']}")
        print(f"  ZIP originals: {len(bundle['zip_moves'])} ({human_size(bundle['zip_bytes'])})")
        for move in bundle["zip_moves"]:
            entry = move["entry"]
            location_label = "already in Janitor" if entry["zip_location"] == "old_review" else "Downloads"
            print(f"    ZIP  {entry['zip_rel']} [{location_label}]")
        print("  Extracted:")
        print(f"    {bundle['extracted_rel']}")
        print(f"  => {bundle['bundle'].relative_to(ROOT)}")
        print()

    reason_counts = defaultdict(int)
    for _, reason in skipped:
        reason_counts[reason] += 1

    if reason_counts:
        print("SKIPPED REASONS")
        print("-" * 78)
        for reason, count in sorted(reason_counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"{count:4}  {reason}")
        print()

    print(f"Plan CSV: {PLAN_CSV}")
    print()

    if not APPLY:
        print("DRY RUN ONLY.")
        print("No files were moved.")
        print()
        print("To apply:")
        print("  python3 scripts/janitor_pair_zips.py --apply")
        print()
        return

    problems = []
    for bundle in bundles:
        if not bundle["extracted"].exists():
            problems.append(f"Missing extracted: {bundle['extracted']}")
        if bundle["extracted_dest"].exists():
            problems.append(f"Destination exists: {bundle['extracted_dest']}")
        for move in bundle["zip_moves"]:
            if not move["source"].is_file():
                problems.append(f"Missing ZIP: {move['source']}")
            if move["destination"].exists():
                problems.append(f"Destination exists: {move['destination']}")

    if problems:
        print("PRE-FLIGHT FAILED")
        print("=" * 78)
        for problem in problems:
            print(f"ERROR: {problem}")
        print("\nNothing was moved.")
        sys.exit(1)

    answer = input(
        f"Move {total_zip_count} ZIPs and {len(bundles)} extracted objects "
        f"into paired review bundles? [yes/NO] "
    )
    if answer != "yes":
        print("Aborted.")
        sys.exit(1)

    moved_zips = 0
    moved_zip_bytes = 0
    moved_extracted = 0

    for bundle in bundles:
        bundle_dir = bundle["bundle"]
        for move in bundle["zip_moves"]:
            src = move["source"]
            dst = move["destination"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved_zips += 1
            moved_zip_bytes += int(move["entry"]["crc_row"]["zip_size_bytes"])

        src = bundle["extracted"]
        dst = bundle["extracted_dest"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved_extracted += 1

        info = bundle_dir / "PAIR_INFO.txt"
        with open(info, "w", encoding="utf-8") as f:
            f.write("Downloads Janitor paired bundle\n")
            f.write("=" * 70 + "\n\n")
            f.write("Original ZIP paths:\n")
            for move in bundle["zip_moves"]:
                f.write(f"  {move['entry']['zip_rel']}\n")
            f.write("\nOriginal extracted path:\n")
            f.write(f"  {bundle['extracted_rel']}\n")
            f.write("\nAll ZIP contents were CRC_VERIFIED against the extracted copy before pairing.\n")

    if OLD_ZIP_REVIEW.exists():
        for dirpath, _, _ in os.walk(OLD_ZIP_REVIEW, topdown=False):
            try:
                Path(dirpath).rmdir()
            except OSError:
                pass

    print()
    print("=" * 78)
    print(f"Moved ZIPs:       {moved_zips}")
    print(f"ZIP size:         {human_size(moved_zip_bytes)}")
    print(f"Extracted items:  {moved_extracted}")
    print()
    print("Paired review root:")
    print(PAIR_ROOT)
    print()
    print("Nothing was deleted.")


if __name__ == "__main__":
    main()

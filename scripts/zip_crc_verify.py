#!/usr/bin/env python3

import csv
import os
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path.home() / "Downloads"
INPUT = ROOT / "_Janitor" / "reports" / "zip_extracted_report.csv"
OUTPUT = ROOT / "_Janitor" / "reports" / "zip_crc_verified.csv"
TEXT = ROOT / "_Janitor" / "reports" / "zip_crc_verified.txt"


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def crc32_file(path):
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def usable_info(info):
    if info.is_dir():
        return False
    if info.filename.startswith("__MACOSX/"):
        return False
    name = Path(info.filename).name
    if name == ".DS_Store" or name.startswith("._"):
        return False
    return True


def is_zip_symlink(info):
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def local_path_for(info, base, strip_root):
    parts = Path(info.filename).parts
    if strip_root:
        if not parts or parts[0] != strip_root:
            return None
        parts = parts[1:]
    if not parts:
        return None
    return base.joinpath(*parts)


def crc32_bytes(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def crc_for_local_target(info, target):
    if is_zip_symlink(info):
        if not target.is_symlink():
            raise RuntimeError("ZIP entry is symlink but local target is not")
        link_text = os.readlink(target).encode("utf-8")
        return crc32_bytes(link_text)
    return crc32_file(target)


def main():
    if not INPUT.is_file():
        print(f"Missing input report: {INPUT}", file=sys.stderr)
        sys.exit(1)

    with open(INPUT, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    exact_rows = [row for row in rows if row.get("status") == "EXACT_EXTRACTED"]
    results = []
    verified_count = 0
    verified_bytes = 0
    failed_count = 0

    print()
    print("ZIP CRC VERIFY")
    print("=" * 70)
    print(f"EXACT_EXTRACTED ZIPs to verify: {len(exact_rows)}")
    print()

    for index, row in enumerate(exact_rows, 1):
        zip_path = ROOT / row["zip_path"]
        base = Path(row["candidate_base"])
        strip_root = (row.get("strip_root") or "").strip()

        checked = 0
        mismatched = []
        missing = []
        errors = []

        print(f"[{index:3}/{len(exact_rows)}] {row['zip_path']}")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    if not usable_info(info):
                        continue

                    target = local_path_for(info, base, strip_root)
                    if target is None:
                        continue

                    if is_zip_symlink(info):
                        exists = target.exists() or target.is_symlink()
                    else:
                        exists = target.is_file()

                    if not exists:
                        missing.append(str(target))
                        continue

                    try:
                        local_crc = crc_for_local_target(info, target)
                    except Exception as e:
                        errors.append(f"{target}: {e}")
                        continue

                    checked += 1
                    if local_crc != info.CRC:
                        mismatched.append(str(target))

        except Exception as e:
            errors.append(str(e))

        expected = int(row["payload_files"])

        if checked == expected and not mismatched and not missing and not errors:
            crc_status = "CRC_VERIFIED"
            verified_count += 1
            verified_bytes += int(row["zip_size_bytes"])
        else:
            crc_status = "CRC_FAILED"
            failed_count += 1

        results.append({
            "crc_status": crc_status,
            "zip_path": row["zip_path"],
            "zip_size_bytes": row["zip_size_bytes"],
            "zip_size": row["zip_size"],
            "candidate_base": row["candidate_base"],
            "strip_root": strip_root,
            "expected_files": expected,
            "checked_files": checked,
            "mismatch_count": len(mismatched),
            "missing_count": len(missing),
            "error_count": len(errors),
            "mismatch_examples": " | ".join(mismatched[:10]),
            "missing_examples": " | ".join(missing[:10]),
            "error_examples": " | ".join(errors[:10]),
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    if results:
        with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
    else:
        OUTPUT.write_text("", encoding="utf-8")

    with open(TEXT, "w", encoding="utf-8") as f:
        f.write("ZIP CRC VERIFY\n")
        f.write("=" * 80 + "\n\n")
        for row in results:
            f.write(f"{row['crc_status']}\n")
            f.write(f"ZIP:       {row['zip_path']}\n")
            f.write(f"Size:      {row['zip_size']}\n")
            f.write(f"Candidate: {row['candidate_base']}\n")
            f.write(f"Checked:   {row['checked_files']}/{row['expected_files']}\n")
            if row["mismatch_count"]:
                f.write(f"CRC mismatch: {row['mismatch_count']}\n")
            if row["missing_count"]:
                f.write(f"Missing:      {row['missing_count']}\n")
            if row["error_count"]:
                f.write(f"Errors:       {row['error_count']}\n")
            f.write("\n")

    print()
    print("=" * 70)
    print(f"CRC_VERIFIED: {verified_count}")
    print(f"CRC_FAILED:   {failed_count}")
    print(f"Verified ZIP size: {human_size(verified_bytes)}")
    print()
    print(f"CSV:  {OUTPUT}")
    print(f"Text: {TEXT}")
    print()
    print("NO FILES WERE MODIFIED.")


if __name__ == "__main__":
    main()

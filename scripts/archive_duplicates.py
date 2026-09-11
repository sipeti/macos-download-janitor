#!/usr/bin/env python3

import argparse
import csv
import hashlib
import zipfile
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = Path.home() / "Downloads"


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024.0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def usable_zip_entry(info):
    if info.is_dir():
        return False
    name = info.filename
    base = Path(name).name
    return not name.startswith("__MACOSX/") and base != ".DS_Store" and not base.startswith("._")


def zip_logical_fingerprint(path):
    try:
        with zipfile.ZipFile(path, "r") as zf:
            items = []
            for info in zf.infolist():
                if not usable_zip_entry(info):
                    continue
                normalized = info.filename.replace("\\", "/")
                items.append((normalized, info.file_size, info.CRC))
            items.sort()
    except Exception:
        return None

    h = hashlib.sha256()
    for name, size, crc in items:
        h.update(name.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(str(size).encode())
        h.update(b"\0")
        h.update(f"{crc:08x}".encode())
        h.update(b"\n")
    return h.hexdigest()


def iter_archives(root, kind):
    exts = {".zip", ".dmg"} if kind == "all" else {f".{kind}"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) >= 2 and rel.parts[0] == "_Janitor" and rel.parts[1] == "reports":
            continue
        yield path, rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--kind", choices=["all", "zip", "dmg"], default="all")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_out = report_dir / f"archive_duplicates_{ns.kind}.csv"
    txt_out = report_dir / f"archive_duplicates_{ns.kind}.txt"

    archives = []
    by_size = defaultdict(list)
    for path, rel in iter_archives(root, ns.kind):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        row = {"path": path, "rel": rel, "size": size, "ext": path.suffix.lower()}
        archives.append(row)
        by_size[(row["ext"], size)].append(row)

    exact_groups = []
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue
        hashes = defaultdict(list)
        for row in candidates:
            try:
                hashes[sha256_file(row["path"])].append(row)
            except OSError:
                pass
        exact_groups.extend(group for group in hashes.values() if len(group) > 1)

    logical_groups = []
    if ns.kind in {"all", "zip"}:
        fps = defaultdict(list)
        for row in archives:
            if row["ext"] != ".zip":
                continue
            fp = zip_logical_fingerprint(row["path"])
            if fp:
                fps[fp].append(row)
        logical_groups = [group for group in fps.values() if len(group) > 1]

    exact_sets = {frozenset(str(r["path"]) for r in g) for g in exact_groups}
    logical_only = [g for g in logical_groups if frozenset(str(r["path"]) for r in g) not in exact_sets]

    rows = []
    gid = 0
    for group_type, groups in (("EXACT_BYTES", exact_groups), ("ZIP_LOGICAL_CONTENT", logical_only)):
        for group in groups:
            gid += 1
            for r in sorted(group, key=lambda x: str(x["rel"]).lower()):
                rows.append({
                    "group_id": gid,
                    "group_type": group_type,
                    "kind": r["ext"].lstrip("."),
                    "path": str(r["rel"]),
                    "size_bytes": r["size"],
                    "size": human_size(r["size"]),
                })

    fields = ["group_id", "group_type", "kind", "path", "size_bytes", "size"]
    with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    with open(txt_out, "w", encoding="utf-8") as f:
        f.write("ARCHIVE DUPLICATE AUDIT\n" + "=" * 80 + "\n\n")
        for group_type, groups in (("EXACT_BYTES", exact_groups), ("ZIP_LOGICAL_CONTENT", logical_only)):
            f.write(f"{group_type}\n" + "-" * 80 + "\n")
            for i, group in enumerate(groups, 1):
                f.write(f"Group {i}: {len(group)} files\n")
                for r in group:
                    f.write(f"  {human_size(r['size']):>10}  {r['rel']}\n")
                f.write("\n")
            f.write("\n")

    exact_files = sum(len(g) for g in exact_groups)
    logical_files = sum(len(g) for g in logical_only)
    print("\nARCHIVE DUPLICATE AUDIT")
    print("=" * 72)
    print(f"Archives scanned:          {len(archives)}")
    print(f"Exact-byte groups:         {len(exact_groups)} ({exact_files} files)")
    print(f"Logical ZIP groups:        {len(logical_only)} ({logical_files} files)")
    print(f"CSV:  {csv_out}")
    print(f"Text: {txt_out}")
    print("NO FILES WERE MODIFIED.")


if __name__ == "__main__":
    main()

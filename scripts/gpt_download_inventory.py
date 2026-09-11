#!/usr/bin/env python3

import csv
import plistlib
import subprocess
import zipfile
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path.home() / "Downloads"
OUT_DIR = ROOT / "fromGPT_inventory"
CSV_OUT = OUT_DIR / "gpt_inventory.csv"
ZIP_OUT = OUT_DIR / "gpt_zip_contents.txt"

SKIP_DIRS = {"fromGPT", "fromGPT_inventory", "_Janitor"}
ORIGIN_MARKERS = ("chatgpt.com", "openai.com", "oaiusercontent.com")

CATEGORIES = {
    "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp", ".svg"},
    "zips": {".zip"},
    "csv": {".csv"},
    "pdf": {".pdf"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"},
    "documents": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".ods"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".sh", ".bash", ".zsh", ".json", ".yaml", ".yml", ".xml", ".sql", ".md", ".txt"},
}


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def category_for(path):
    ext = path.suffix.lower()
    for category, extensions in CATEGORIES.items():
        if ext in extensions:
            return category
    return "other"


def where_froms(path):
    try:
        result = subprocess.run(
            ["xattr", "-px", "com.apple.metadata:kMDItemWhereFroms", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    try:
        raw = bytes.fromhex("".join(result.stdout.split()))
        value = plistlib.loads(raw)
        if isinstance(value, list):
            return [str(v) for v in value]
    except Exception:
        pass
    return []


def is_gpt_origin(path):
    origins = where_froms(path)
    return origins, any(marker in origin.lower() for origin in origins for marker in ORIGIN_MARKERS)


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(ROOT).parts[:-1]
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        yield path


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    zip_details = []
    unreadable_zips = 0

    for path in iter_files():
        origins, matched = is_gpt_origin(path)
        if not matched:
            continue
        try:
            st = path.stat()
        except OSError:
            continue

        category = category_for(path)
        row = {
            "path": str(path.relative_to(ROOT)),
            "category": category,
            "size_bytes": st.st_size,
            "size": human_size(st.st_size),
            "mtime": int(st.st_mtime),
            "origins": " | ".join(origins),
        }
        rows.append(row)

        if category == "zips":
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    names = [i.filename for i in zf.infolist() if not i.is_dir()]
                zip_details.append((row["path"], len(names), names[:200]))
            except Exception as e:
                unreadable_zips += 1
                zip_details.append((row["path"], -1, [f"ERROR: {e}"]))

    rows.sort(key=lambda r: (-int(r["size_bytes"]), r["path"].lower()))

    fields = ["path", "category", "size_bytes", "size", "mtime", "origins"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with open(ZIP_OUT, "w", encoding="utf-8") as f:
        for path, count, names in zip_details:
            f.write(f"ZIP: {path}\n")
            f.write(f"Entries: {count if count >= 0 else 'unreadable'}\n")
            for name in names:
                f.write(f"  {name}\n")
            f.write("\n")

    counts = Counter(r["category"] for r in rows)
    sizes = defaultdict(int)
    for row in rows:
        sizes[row["category"]] += int(row["size_bytes"])

    print()
    print("CHATGPT DOWNLOAD INVENTORY")
    print("=" * 60)
    for category in sorted(counts, key=lambda c: -sizes[c]):
        print(f"{category:15} {counts[category]:5} files  {human_size(sizes[category]):>12}")
    print("-" * 60)
    print(f"TOTAL             {len(rows):5} files  {human_size(sum(sizes.values())):>12}")
    print(f"ZIP files: {counts.get('zips', 0)}")
    print(f"Unreadable ZIPs: {unreadable_zips}")
    print(f"CSV: {CSV_OUT}")
    print(f"ZIP report: {ZIP_OUT}")
    print("NO FILES WERE MODIFIED.")


if __name__ == "__main__":
    main()

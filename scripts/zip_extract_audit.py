#!/usr/bin/env python3

import csv
import re
import sys
import zipfile
from pathlib import Path
from collections import Counter

ROOT = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else (Path.home() / "Downloads").resolve()
REPORT_DIR = ROOT / "_Janitor" / "reports"
CSV_OUT = REPORT_DIR / "zip_extracted_report.csv"
TXT_OUT = REPORT_DIR / "zip_extracted_report.txt"

IGNORED_DIRS = {
    "_Janitor", "fromGPT", "fromGPT_inventory", ".git", ".svn", ".hg",
    "node_modules", ".venv", "__pycache__"
}


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def normalize_stem(stem):
    return re.sub(r" \(\d+\)$", "", stem)


def usable_info(info):
    if info.is_dir():
        return False
    if info.filename.startswith("__MACOSX/"):
        return False
    name = Path(info.filename).name
    if name == ".DS_Store" or name.startswith("._"):
        return False
    return True


def common_root_for(infos):
    parts = [Path(i.filename).parts for i in infos]
    if not parts or any(len(p) < 2 for p in parts):
        return ""
    roots = {p[0] for p in parts}
    return next(iter(roots)) if len(roots) == 1 else ""


def build_candidates(zip_path, common_root):
    parent = zip_path.parent
    stem = zip_path.stem
    norm = normalize_stem(stem)
    candidates = []
    seen = set()

    def add(label, base, strip_root=None):
        key = (str(base), strip_root or "")
        if key in seen:
            return
        seen.add(key)
        candidates.append({"label": label, "base": base, "strip_root": strip_root or ""})

    add("same_dir", parent)
    add("folder_stem", parent / stem)
    if norm != stem:
        add("folder_normalized_stem", parent / norm)

    if parent != ROOT:
        add("downloads_root", ROOT)
        add("downloads_root_folder_stem", ROOT / stem)
        if norm != stem:
            add("downloads_root_folder_normalized_stem", ROOT / norm)

    if common_root:
        add("same_dir_strip_common_root", parent, common_root)
        add("folder_stem_strip_common_root", parent / stem, common_root)
        if norm != stem:
            add("folder_normalized_stem_strip_common_root", parent / norm, common_root)

        if parent != ROOT:
            add("downloads_root_strip_common_root", ROOT, common_root)
            add("downloads_root_folder_stem_strip_common_root", ROOT / stem, common_root)
            add("downloads_root_common_root_folder", ROOT / common_root, common_root)
            if norm != stem:
                add("downloads_root_folder_normalized_stem_strip_common_root", ROOT / norm, common_root)

    return candidates


def target_for(info, candidate):
    parts = Path(info.filename).parts
    strip_root = candidate["strip_root"]
    if strip_root:
        if not parts or parts[0] != strip_root:
            return None
        parts = parts[1:]
    if not parts:
        return None
    return candidate["base"].joinpath(*parts)


def evaluate_candidate(infos, candidate):
    matched_files = 0
    matched_bytes = 0
    missing = []
    mismatched = []

    for info in infos:
        target = target_for(info, candidate)
        if target is None:
            continue
        if not target.is_file():
            missing.append(str(target))
            continue
        try:
            size = target.stat().st_size
        except OSError:
            missing.append(str(target))
            continue
        if size != info.file_size:
            mismatched.append(f"{target} ({size} != {info.file_size})")
            continue
        matched_files += 1
        matched_bytes += info.file_size

    total_files = len(infos)
    total_bytes = sum(i.file_size for i in infos)
    ratio = (matched_files / total_files) if total_files else 0.0

    return {
        **candidate,
        "matched_files": matched_files,
        "matched_bytes": matched_bytes,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "ratio": ratio,
        "missing": missing,
        "mismatched": mismatched,
    }


def classify(best):
    total = best["total_files"]
    matched = best["matched_files"]
    ratio = best["ratio"]
    if total and matched == total:
        return "EXACT_EXTRACTED"
    if total >= 3 and ratio >= 0.95:
        return "LIKELY_EXTRACTED"
    if total >= 3 and ratio >= 0.50:
        return "PARTIAL_MATCH"
    if matched:
        return "WEAK_MATCH"
    return "NO_MATCH"


def scan_zips():
    for p in ROOT.rglob("*.zip"):
        try:
            rel_parts = p.relative_to(ROOT).parts[:-1]
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        yield p


def main():
    rows = []

    for zip_path in scan_zips():
        rel = zip_path.relative_to(ROOT)
        try:
            zip_size = zip_path.stat().st_size
        except OSError:
            zip_size = 0

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                infos = [i for i in zf.infolist() if usable_info(i)]
        except zipfile.BadZipFile:
            rows.append({
                "status": "BAD_ZIP", "zip_path": str(rel), "zip_size_bytes": zip_size,
                "zip_size": human_size(zip_size), "payload_files": 0, "common_root": "",
                "candidate_label": "", "candidate_base": "", "strip_root": "",
                "matched_files": 0, "match_ratio": "0.00", "missing_examples": "",
                "mismatch_examples": ""
            })
            continue

        if not infos:
            rows.append({
                "status": "EMPTY_ZIP", "zip_path": str(rel), "zip_size_bytes": zip_size,
                "zip_size": human_size(zip_size), "payload_files": 0, "common_root": "",
                "candidate_label": "", "candidate_base": "", "strip_root": "",
                "matched_files": 0, "match_ratio": "0.00", "missing_examples": "",
                "mismatch_examples": ""
            })
            continue

        common_root = common_root_for(infos)
        evaluations = [evaluate_candidate(infos, c) for c in build_candidates(zip_path, common_root)]
        best = max(evaluations, key=lambda e: (e["matched_files"], e["matched_bytes"], e["ratio"]))
        status = classify(best)

        rows.append({
            "status": status,
            "zip_path": str(rel),
            "zip_size_bytes": zip_size,
            "zip_size": human_size(zip_size),
            "payload_files": len(infos),
            "common_root": common_root,
            "candidate_label": best["label"],
            "candidate_base": str(best["base"]),
            "strip_root": best["strip_root"],
            "matched_files": best["matched_files"],
            "match_ratio": f"{best['ratio']:.4f}",
            "missing_examples": " | ".join(best["missing"][:10]),
            "mismatch_examples": " | ".join(best["mismatched"][:10]),
        })

    rows.sort(key=lambda r: (-int(r["zip_size_bytes"]), r["zip_path"].lower()))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    fields = [
        "status", "zip_path", "zip_size_bytes", "zip_size", "payload_files", "common_root",
        "candidate_label", "candidate_base", "strip_root", "matched_files", "match_ratio",
        "missing_examples", "mismatch_examples"
    ]

    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(r["status"] for r in rows)
    with open(TXT_OUT, "w", encoding="utf-8") as f:
        f.write("ZIP EXTRACT AUDIT\n")
        f.write("=" * 80 + "\n")
        f.write(f"Root: {ROOT}\n\n")
        for row in rows:
            f.write(f"{row['status']}\n")
            f.write(f"ZIP:       {row['zip_path']}\n")
            f.write(f"Size:      {row['zip_size']}\n")
            f.write(f"Files:     {row['matched_files']}/{row['payload_files']}\n")
            if row["common_root"]:
                f.write(f"Common:    {row['common_root']}\n")
            if row["candidate_base"]:
                f.write(f"Candidate: {row['candidate_base']}\n")
                f.write(f"Label:     {row['candidate_label']}\n")
            if row["missing_examples"]:
                f.write(f"Missing:   {row['missing_examples']}\n")
            if row["mismatch_examples"]:
                f.write(f"Mismatch:  {row['mismatch_examples']}\n")
            f.write("\n")

    print()
    print("ZIP EXTRACT AUDIT")
    print("=" * 60)
    print(f"Root:              {ROOT}")
    print(f"ZIP scanned:       {len(rows)}")
    for status in ("EXACT_EXTRACTED", "LIKELY_EXTRACTED", "PARTIAL_MATCH", "WEAK_MATCH", "NO_MATCH", "EMPTY_ZIP", "BAD_ZIP"):
        print(f"{status + ':':20} {counts.get(status, 0)}")
    print(f"CSV report:  {CSV_OUT}")
    print(f"Text report: {TXT_OUT}")
    print("NO FILES WERE MODIFIED.")


if __name__ == "__main__":
    main()

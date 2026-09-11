#!/usr/bin/env python3

import argparse
import csv
import plistlib
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_ROOT = Path.home() / "Downloads"

MANAGED_DIRS = {
    "_Janitor", "fromGPT", "fromGPT_inventory", "_ZIP", "_PDF", "_DMG",
    "_Media", "_Documents", "_Installers",
}

GPT_MARKERS = ("chatgpt.com", "openai.com", "oaiusercontent.com")

EXTENSIONS = {
    "pdf": {".pdf"},
    "zip": {".zip"},
    "dmg": {".dmg"},
    "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp", ".svg"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"},
    "csv": {".csv"},
    "documents": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".ods"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".sh", ".bash", ".zsh", ".json", ".yaml", ".yml", ".xml", ".sql", ".md", ".txt"},
    "installer": {".pkg", ".mpkg"},
}

SOURCE_RULES = [
    ("ChatGPT", GPT_MARKERS),
    ("YouTube", ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com")),
    ("Facebook", ("facebook.com", "fbcdn.net", "fb.watch")),
    ("Instagram", ("instagram.com", "cdninstagram.com")),
    ("TikTok", ("tiktok.com", "tiktokcdn.com", "muscdn.com")),
    ("X-Twitter", ("twitter.com", "x.com", "twimg.com")),
    ("Vimeo", ("vimeo.com", "vimeocdn.com")),
    ("GoogleDrive", ("drive.google.com", "docs.google.com")),
    ("Dropbox", ("dropbox.com", "dropboxusercontent.com")),
]

VENDOR_RULES = [
    ("Raiffeisen", ("raiffeisen", "raiffeisen.hu")),
    ("Signal", ("signal iduna", "signal-iduna", "signal.hu")),
    ("NAV", ("nav.gov.hu", "nemzeti adó- és vámhivatal", "nemzeti ado- es vamhivatal")),
    ("OTP", ("otpbank", "otp bank", "otpbank.hu")),
    ("Erste", ("erste bank", "erstebank", "erstebank.hu")),
    ("K&H", ("k&h bank", "kh.hu", "k&H")),
    ("CIB", ("cib bank", "cib.hu")),
    ("UniCredit", ("unicredit", "unicreditbank.hu")),
    ("MBH", ("mbh bank", "mbhbank", "mkb bank", "takarékbank", "takarekbank")),
    ("MVM", ("mvm", "mvmnext", "mvmnext.hu")),
    ("EON", ("eon.hu", "e.on", "eon energia")),
    ("One", ("one.hu", "vodafone", "vodafone.hu")),
    ("Telekom", ("telekom.hu", "magyar telekom")),
    ("Allianz", ("allianz", "allianz.hu")),
    ("Generali", ("generali", "generali.hu")),
    ("Groupama", ("groupama", "groupama.hu")),
]

PDF_KIND_RULES = {
    "Statements": (
        "bankszámlakivonat", "bankszamlakivonat", "számlakivonat", "szamlakivonat",
        "account statement", "nyitó egyenleg", "nyito egyenleg", "záró egyenleg", "zaro egyenleg",
    ),
    "Invoices": (
        "számla sorszáma", "szamla sorszama", "fizetendő", "fizetendo", "teljesítés dátuma",
        "teljesites datuma", "invoice number", "invoice", "számla", "szamla",
    ),
    "Insurance": (
        "biztosítási kötvény", "biztositasi kotveny", "kötvényszám", "kotvenyszam",
        "biztosítás", "biztositas", "insurance policy", "díjértesítő", "dijertesito",
    ),
    "Tax": (
        "adófolyószámla", "adofolyoszamla", "adóbevallás", "adobevallas", "nav.gov.hu",
        "nemzeti adó- és vámhivatal", "nemzeti ado- es vamhivatal",
    ),
    "Contracts": (
        "szerződés", "szerzodes", "megállapodás", "megallapodas", "contract", "agreement",
    ),
}


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024.0


def category_for(path):
    ext = path.suffix.lower()
    for category, extensions in EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"


def where_froms(path):
    try:
        result = subprocess.run(
            ["xattr", "-px", "com.apple.metadata:kMDItemWhereFroms", str(path)],
            capture_output=True, text=True, check=True,
        )
        raw = bytes.fromhex("".join(result.stdout.split()))
        value = plistlib.loads(raw)
        if isinstance(value, list):
            return [str(v) for v in value]
    except Exception:
        pass
    return []


def spotlight_text(path):
    try:
        result = subprocess.run(
            ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        text = result.stdout.strip()
        if text and text != "(null)":
            return text[:250000]
    except Exception:
        pass
    return ""


def source_group(origins):
    lowered = " ".join(origins).lower()
    for label, markers in SOURCE_RULES:
        if any(marker.lower() in lowered for marker in markers):
            return label

    for origin in origins:
        try:
            host = (urlparse(origin).hostname or "").lower()
        except Exception:
            host = ""
        if host:
            host = re.sub(r"^www\.", "", host)
            return re.sub(r"[^a-zA-Z0-9._-]+", "_", host)[:80]

    return "Unknown"


def is_gpt(origins):
    lowered = " ".join(origins).lower()
    return any(marker in lowered for marker in GPT_MARKERS)


def match_vendor(haystack):
    h = haystack.lower()
    scores = []
    for vendor, markers in VENDOR_RULES:
        score = sum(1 for marker in markers if marker.lower() in h)
        if score:
            scores.append((score, vendor))
    return max(scores)[1] if scores else "Unknown"


def classify_pdf(path, origins):
    text = spotlight_text(path)
    haystack = "\n".join([path.name, " ".join(origins), text]).lower()
    vendor = match_vendor(haystack)

    kind_scores = {}
    reasons = []
    for kind, markers in PDF_KIND_RULES.items():
        score = 0
        hit = []
        for marker in markers:
            if marker.lower() in haystack:
                score += 2 if len(marker) > 10 else 1
                hit.append(marker)
        kind_scores[kind] = score
        if hit:
            reasons.append(f"{kind}: {', '.join(hit[:4])}")

    kind = max(kind_scores, key=kind_scores.get)
    score = kind_scores[kind]
    if score == 0:
        kind = "Other"

    if kind == "Statements":
        dest = Path("_PDF") / "Statements" / vendor
    elif kind == "Invoices":
        dest = Path("_PDF") / "Invoices" / vendor
    elif kind == "Insurance":
        dest = Path("_PDF") / "Insurance" / vendor
    elif kind == "Tax":
        dest = Path("_PDF") / "Tax" / vendor
    elif kind == "Contracts":
        dest = Path("_PDF") / "Contracts" / vendor
    elif vendor != "Unknown":
        dest = Path("_PDF") / "BySource" / vendor
    else:
        dest = Path("_PDF") / "Other"

    confidence = "high" if score >= 4 else "medium" if score >= 2 else "low"
    return kind, vendor, confidence, "; ".join(reasons), dest


def gpt_destination(category):
    mapping = {
        "images": "images", "video": "video", "audio": "audio", "pdf": "pdf",
        "zip": "zips", "csv": "csv", "documents": "documents", "code": "code",
    }
    return Path("fromGPT") / mapping.get(category, "other")


def destination_for(path, category, origins):
    if is_gpt(origins):
        return gpt_destination(category), {"pdf_kind": "", "vendor": "ChatGPT", "confidence": "high", "reason": "ChatGPT/OpenAI origin"}

    if category == "pdf":
        kind, vendor, confidence, reason, dest = classify_pdf(path, origins)
        return dest, {"pdf_kind": kind, "vendor": vendor, "confidence": confidence, "reason": reason}

    if category == "zip":
        return Path("_ZIP"), {}
    if category == "dmg":
        return Path("_DMG"), {}
    if category == "installer":
        return Path("_Installers"), {}
    if category in {"images", "video", "audio"}:
        source = source_group(origins)
        return Path("_Media") / source / category, {"vendor": source}
    if category in {"documents", "csv", "code"}:
        return Path("_Documents") / category, {}

    return None, {}


def wanted(category, origins, only):
    if only == "all":
        return True
    if only == "gpt":
        return is_gpt(origins)
    if only == "media":
        return category in {"images", "video", "audio"}
    if only == "documents":
        return category in {"documents", "csv", "code"}
    return category == only


def collision_safe(path):
    if not path.exists():
        return path
    for i in range(1, 10000):
        candidate = path.with_name(f"{path.stem}__{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find free destination name for {path}")


def scan(root, only):
    rows = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in MANAGED_DIRS:
            continue
        if path.name == ".DS_Store":
            continue

        category = category_for(path)
        origins = where_froms(path)
        if not wanted(category, origins, only):
            continue

        is_top = len(rel.parts) == 1
        dest_dir, meta = destination_for(path, category, origins)
        if not is_top:
            action = "WARN_NESTED"
            dest = ""
        elif dest_dir is None:
            action = "LEAVE"
            dest = ""
        else:
            action = "MOVE"
            dest = str(dest_dir / path.name)

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        rows.append({
            "path": str(rel), "top_level": "yes" if is_top else "no", "category": category,
            "size_bytes": size, "size": human_size(size), "action": action, "destination": dest,
            "source_group": source_group(origins), "origins": " | ".join(origins),
            "pdf_kind": meta.get("pdf_kind", ""), "vendor": meta.get("vendor", ""),
            "confidence": meta.get("confidence", ""), "reason": meta.get("reason", ""),
        })
    rows.sort(key=lambda r: (0 if r["action"] == "MOVE" else 1, -int(r["size_bytes"]), r["path"].lower()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--only", choices=["all", "pdf", "zip", "dmg", "media", "gpt", "documents"], default="all")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_out = report_dir / f"downloads_sort_plan_{ns.only}.csv"

    rows = scan(root, ns.only)
    fields = ["path", "top_level", "category", "size_bytes", "size", "action", "destination", "source_group", "origins", "pdf_kind", "vendor", "confidence", "reason"]
    with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    counts = Counter(r["action"] for r in rows)
    move_bytes = sum(int(r["size_bytes"]) for r in rows if r["action"] == "MOVE")
    print("\nDOWNLOADS ORGANIZER")
    print("=" * 78)
    print(f"Root:            {root}")
    print(f"Filter:          {ns.only}")
    print(f"Move candidates: {counts['MOVE']} ({human_size(move_bytes)})")
    print(f"Nested warnings: {counts['WARN_NESTED']}")
    print(f"Left in place:   {counts['LEAVE']}")
    print(f"Report:          {csv_out}\n")

    for r in rows:
        if r["action"] == "MOVE":
            extra = ""
            if r["category"] == "pdf":
                extra = f" [{r['pdf_kind']}/{r['vendor']}/{r['confidence']}]"
            print(f"MOVE {r['size']:>10}  {r['path']} -> {r['destination']}{extra}")
        elif r["action"] == "WARN_NESTED":
            print(f"WARN nested: {r['path']} [{r['category']}, source={r['source_group']}]")

    if not ns.apply:
        print("\nDRY RUN ONLY. No files were moved.")
        return

    moves = [r for r in rows if r["action"] == "MOVE"]
    if not ns.yes:
        answer = input(f"\nMove {len(moves)} top-level files according to this plan? [yes/NO] ")
        if answer != "yes":
            print("Aborted.")
            return

    moved = 0
    for r in moves:
        src = root / r["path"]
        if not src.is_file():
            print(f"SKIP missing: {src}")
            continue
        dst = collision_safe(root / r["destination"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    print(f"\nMoved {moved} files. Nothing was deleted.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import argparse
import csv
import plistlib
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_ROOT = Path.home() / "Downloads"

MANAGED_DIRS = {
    "_Janitor", "fromGPT", "fromGPT_inventory", "_ZIP", "_PDF", "_DMG",
    "_Media", "_Documents", "_Installers",
}

GPT_MARKERS = ("chatgpt.com", "openai.com", "oaiusercontent.com")
GPT_FILENAME_PATTERNS = (
    re.compile(r"^chatgpt[ _-]+image\b", re.I),
    re.compile(r"\bchatgpt\b", re.I),
)

SENSITIVE_FILENAME_PATTERNS = (
    ("2fa", re.compile(r"(?:^|[^a-z0-9])2fa(?:[^a-z0-9]|$)", re.I)),
    ("totp", re.compile(r"\btotp\b", re.I)),
    ("recovery-code", re.compile(r"\brecovery[ _-]*codes?\b", re.I)),
    ("backup-code", re.compile(r"\bbackup[ _-]*codes?\b", re.I)),
    ("secret", re.compile(r"\bsecret(?:s)?\b", re.I)),
    ("private-key", re.compile(r"\bprivate[ _-]*key\b", re.I)),
    ("password", re.compile(r"\bpass(?:word|wd)s?\b", re.I)),
    ("credential", re.compile(r"\bcredentials?\b", re.I)),
    ("token", re.compile(r"\b(?:api[ _-]*)?tokens?\b", re.I)),
    ("identity-document", re.compile(r"\b(?:passport|szemelyi|személyi|lakcim|lakcím|taj[_ -]?kartya|taj[_ -]?kártya)\b", re.I)),
)

TEMP_OFFICE_RE = re.compile(r"^~\$", re.I)

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

# Canonical source labels. Rules are intentionally generic and contain no user data.
SOURCE_RULES = [
    ("ChatGPT", GPT_MARKERS),
    ("YouTube", ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "ytcontent.com")),
    ("Facebook", ("facebook.com", "fbcdn.net", "fb.watch")),
    ("Messenger", ("messenger.com",)),
    ("Instagram", ("instagram.com", "cdninstagram.com")),
    ("TikTok", ("tiktok.com", "tiktokcdn.com", "muscdn.com", "tikcdn.io", "ssstik.io")),
    ("X-Twitter", ("twitter.com", "x.com", "twimg.com")),
    ("Vimeo", ("vimeo.com", "vimeocdn.com")),
    ("GoogleDrive", ("drive.google.com", "docs.google.com", "drive.usercontent.google.com", "video-downloads.googleusercontent.com")),
    ("Gmail", ("mail-attachment.googleusercontent.com",)),
    ("WeTransfer", ("wetransfer.com", "download.wetransfer.com")),
    ("Dropbox", ("dropbox.com", "dropboxusercontent.com")),
    ("Telegram", ("web.telegram.org", "telegram.org")),
]

# Common downloader/CDN hosts that usually hide the real source. Keep them out of the
# directory tree; infer a canonical service only when the filename also supports it.
YOUTUBE_DOWNLOADER_MARKERS = (
    "yt-dl.click", "savenow.to", "oceansaver.in", "iamworker.com",
    "apiyoutube.cc", "vidssave.com", "dlsrv.online", "yt1s",
)
YOUTUBE_FILENAME_MARKERS = (
    "youtube", "ytdown", "yt2mp3", "shorts", "1080p", "720p", "480p", "360p",
)
TIKTOK_FILENAME_MARKERS = ("tiktok", "ssstik", "snaptik")

VENDOR_RULES = [
    ("Raiffeisen", ("raiffeisen", "raiffeisen.hu")),
    ("Signal", ("signal iduna", "signal-iduna", "signal.hu")),
    ("NAV", ("nav.gov.hu", "nemzeti adó- és vámhivatal", "nemzeti ado- es vamhivatal")),
    ("OTP", ("otpbank", "otp bank", "otpbank.hu")),
    ("Erste", ("erste bank", "erstebank", "erstebank.hu")),
    ("K&H", ("k&h bank", "kh.hu")),
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
        "account statement", "bank statement", "statement_", "nyitó egyenleg", "nyito egyenleg",
        "záró egyenleg", "zaro egyenleg",
    ),
    "Invoices": (
        "számla sorszáma", "szamla sorszama", "fizetendő", "fizetendo", "teljesítés dátuma",
        "teljesites datuma", "invoice number", "invoice", "számla", "szamla",
    ),
    "Insurance": (
        "biztosítási kötvény", "biztositasi kotveny", "kötvényszám", "kotvenyszam",
        "biztosítás", "biztositas", "insurance policy", "díjértesítő", "dijertesito", "kotveny", "kötvény",
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


def hostnames(origins):
    result = []
    for origin in origins:
        try:
            host = (urlparse(origin).hostname or "").lower()
        except Exception:
            host = ""
        if host:
            result.append(re.sub(r"^www\.", "", host))
    return result


def source_group(origins, filename=""):
    lowered = " ".join(origins).lower()
    name = filename.lower()

    for label, markers in SOURCE_RULES:
        if any(marker.lower() in lowered for marker in markers):
            return label

    hosts = hostnames(origins)
    if any(any(marker in host for marker in YOUTUBE_DOWNLOADER_MARKERS) for host in hosts):
        if any(marker in name for marker in YOUTUBE_FILENAME_MARKERS):
            return "YouTube"
        return "Downloader"

    if any(marker in name for marker in TIKTOK_FILENAME_MARKERS):
        return "TikTok"

    if hosts:
        # Preserve the original domain only for ordinary sites. This is still useful
        # for downloaded reference images and documents from a known publisher/vendor.
        host = hosts[0]
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", host)[:80]

    return "Unknown"


def gpt_provenance(path, origins):
    lowered = " ".join(origins).lower()
    if any(marker in lowered for marker in GPT_MARKERS):
        return "confirmed"
    if any(pattern.search(path.name) for pattern in GPT_FILENAME_PATTERNS):
        return "probable"
    return "no"


def sensitive_reason(path):
    name = path.name
    for label, pattern in SENSITIVE_FILENAME_PATTERNS:
        if pattern.search(name):
            return label
    return ""


def junk_reason(path, size):
    if TEMP_OFFICE_RE.search(path.name):
        return "office-lock-file"
    if size == 0:
        return "zero-byte-file"
    return ""


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

    # Vendor evidence improves our confidence in a useful bucket but does not invent
    # the document kind. Low/medium-confidence PDFs are staged under _PDF/_Review.
    if score >= 4:
        confidence = "high"
    elif score >= 2 or (score >= 1 and vendor != "Unknown"):
        confidence = "medium"
    else:
        confidence = "low"

    if confidence == "high":
        dest = Path("_PDF") / kind / vendor
    else:
        review_vendor = vendor if vendor != "Unknown" else "Unknown"
        dest = Path("_PDF") / "_Review" / review_vendor

    return kind, vendor, confidence, "; ".join(reasons), dest


def gpt_destination(category, review=False):
    mapping = {
        "images": "images", "video": "video", "audio": "audio", "pdf": "pdf",
        "zip": "zips", "csv": "csv", "documents": "documents", "code": "code",
    }
    base = Path("fromGPT")
    if review:
        base /= "_Review"
    return base / mapping.get(category, "other")


def classify_top_level(path, category, origins, size):
    sensitive = sensitive_reason(path)
    if sensitive:
        return "SENSITIVE", None, {
            "confidence": "blocked", "reason": sensitive, "provenance": gpt_provenance(path, origins),
        }

    junk = junk_reason(path, size)
    if junk:
        return "REVIEW", Path("_Janitor") / "review" / "junk-candidates", {
            "confidence": "review", "reason": junk, "provenance": gpt_provenance(path, origins),
        }

    provenance = gpt_provenance(path, origins)
    if provenance == "confirmed":
        return "MOVE", gpt_destination(category), {
            "confidence": "high", "reason": "ChatGPT/OpenAI origin metadata", "provenance": provenance,
            "vendor": "ChatGPT",
        }
    if provenance == "probable":
        return "REVIEW", gpt_destination(category, review=True), {
            "confidence": "medium", "reason": "ChatGPT-like filename without origin metadata", "provenance": provenance,
            "vendor": "ChatGPT",
        }

    if category == "pdf":
        kind, vendor, confidence, reason, dest = classify_pdf(path, origins)
        action = "MOVE" if confidence == "high" else "REVIEW"
        return action, dest, {
            "pdf_kind": kind, "vendor": vendor, "confidence": confidence, "reason": reason,
            "provenance": provenance,
        }

    if category == "zip":
        return "MOVE", Path("_ZIP"), {"confidence": "high", "provenance": provenance}
    if category == "dmg":
        return "MOVE", Path("_DMG"), {"confidence": "high", "provenance": provenance}
    if category == "installer":
        return "MOVE", Path("_Installers"), {"confidence": "high", "provenance": provenance}
    if category in {"images", "video", "audio"}:
        source = source_group(origins, path.name)
        return "MOVE", Path("_Media") / source / category, {
            "vendor": source, "confidence": "high" if source != "Unknown" else "medium", "provenance": provenance,
        }
    if category in {"documents", "csv", "code"}:
        return "MOVE", Path("_Documents") / category, {"confidence": "high", "provenance": provenance}

    return "LEAVE", None, {"confidence": "none", "provenance": provenance}


def wanted(category, provenance, sensitive, only):
    if only == "all":
        return True
    if only == "gpt":
        return provenance in {"confirmed", "probable"}
    if only == "media":
        return category in {"images", "video", "audio"}
    if only == "documents":
        return category in {"documents", "csv", "code"}
    if only == "sensitive":
        return bool(sensitive)
    if only == "junk":
        return True
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
    """Scan only direct children of Downloads. Nested directories are not traversed."""
    rows = []
    for path in root.iterdir():
        if not path.is_file() or path.name == ".DS_Store":
            continue

        category = category_for(path)
        origins = where_froms(path)
        provenance = gpt_provenance(path, origins)
        sensitive = sensitive_reason(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        if only == "junk" and not junk_reason(path, size):
            continue
        if not wanted(category, provenance, sensitive, only):
            continue

        action, dest_dir, meta = classify_top_level(path, category, origins, size)
        destination = str(dest_dir / path.name) if dest_dir else ""

        rows.append({
            "path": path.name,
            "category": category,
            "size_bytes": size,
            "size": human_size(size),
            "action": action,
            "destination": destination,
            "source_group": source_group(origins, path.name),
            "origins": " | ".join(origins),
            "provenance": meta.get("provenance", provenance),
            "pdf_kind": meta.get("pdf_kind", ""),
            "vendor": meta.get("vendor", ""),
            "confidence": meta.get("confidence", ""),
            "reason": meta.get("reason", ""),
        })

    order = {"SENSITIVE": 0, "REVIEW": 1, "MOVE": 2, "LEAVE": 3}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -int(r["size_bytes"]), r["path"].lower()))
    return rows


def redacted_row(row):
    return {
        **row,
        "path": "[REDACTED]",
        "origins": "[REDACTED]" if row["origins"] else "",
        "destination": str(Path(row["destination"]).parent / "[REDACTED]") if row["destination"] else "",
    }


def print_summary(rows, root, only, report_path):
    counts = Counter(r["action"] for r in rows)
    move_bytes = sum(int(r["size_bytes"]) for r in rows if r["action"] == "MOVE")
    review_bytes = sum(int(r["size_bytes"]) for r in rows if r["action"] == "REVIEW")
    destination_counts = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["destination"]:
            folder = str(Path(r["destination"]).parent)
            destination_counts[folder][0] += 1
            destination_counts[folder][1] += int(r["size_bytes"])

    print("\nDOWNLOADS ORGANIZER")
    print("=" * 78)
    print(f"Root:               {root}")
    print(f"Filter:             {only}")
    print("Scope:              top-level files only (nested directories ignored)")
    print(f"Safe moves:         {counts['MOVE']} ({human_size(move_bytes)})")
    print(f"Review candidates:  {counts['REVIEW']} ({human_size(review_bytes)})")
    print(f"Sensitive blocked:  {counts['SENSITIVE']}")
    print(f"Left in place:      {counts['LEAVE']}")
    print(f"Local report:       {report_path}")

    if destination_counts:
        print("\nDestination summary:")
        for folder, (count, total) in sorted(destination_counts.items(), key=lambda x: (-x[1][1], x[0])):
            print(f"  {count:5}  {human_size(total):>10}  {folder}")


def main():
    ap = argparse.ArgumentParser(description="Privacy-first organizer for top-level ~/Downloads files.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--only", choices=["all", "pdf", "zip", "dmg", "media", "gpt", "documents", "sensitive", "junk"], default="all")
    ap.add_argument("--apply", action="store_true", help="Move safe/high-confidence candidates")
    ap.add_argument("--include-review", action="store_true", help="With --apply, also move review candidates to staging folders")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="Print filenames and detailed decisions")
    ap.add_argument("--redact-report", action="store_true", help="Hide filenames and origin URLs in the CSV report")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_redacted" if ns.redact_report else ""
    csv_out = report_dir / f"downloads_sort_plan_{ns.only}{suffix}.csv"

    rows = scan(root, ns.only)
    fields = [
        "path", "category", "size_bytes", "size", "action", "destination", "source_group",
        "origins", "provenance", "pdf_kind", "vendor", "confidence", "reason",
    ]
    with open(csv_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(redacted_row(r) if ns.redact_report else r for r in rows)

    print_summary(rows, root, ns.only, csv_out)

    if ns.verbose:
        print("\nDetailed decisions:")
        for r in rows:
            extra = ""
            if r["category"] == "pdf":
                extra = f" [{r['pdf_kind']}/{r['vendor']}/{r['confidence']}]"
            if r["action"] == "SENSITIVE":
                print(f"SENSITIVE {r['size']:>10}  {r['path']} [{r['reason']}] - no move")
            elif r["destination"]:
                print(f"{r['action']:<9} {r['size']:>10}  {r['path']} -> {r['destination']}{extra}")
            else:
                print(f"{r['action']:<9} {r['size']:>10}  {r['path']}{extra}")

    if not ns.apply:
        print("\nDRY RUN ONLY. No files were moved.")
        print("Use --verbose to show filenames. Nested folders are intentionally untouched.")
        return

    moves = [r for r in rows if r["action"] == "MOVE"]
    if ns.include_review:
        moves += [r for r in rows if r["action"] == "REVIEW"]

    if not ns.yes:
        review_note = " including review staging" if ns.include_review else ""
        answer = input(f"\nMove {len(moves)} top-level files{review_note}? [yes/NO] ")
        if answer != "yes":
            print("Aborted.")
            return

    moved = 0
    for r in moves:
        src = root / r["path"]
        if not src.is_file() or not r["destination"]:
            continue
        dst = collision_safe(root / r["destination"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1

    print(f"\nMoved {moved} files. Sensitive candidates and nested folders were untouched. Nothing was deleted.")


if __name__ == "__main__":
    main()

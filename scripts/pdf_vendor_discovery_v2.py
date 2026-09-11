#!/usr/bin/env python3

# v2: layered local PDF text extraction for vendor discovery.
# Order: Spotlight -> pdftotext (if installed) -> native macOS PDFKit helper.
# No OCR, no network, top-level Downloads PDFs only.

import argparse
import csv
import re
import shutil
import subprocess
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from downloads_organizer import DEFAULT_ROOT, VENDOR_RULES, match_vendor, where_froms, spotlight_text

REPO_ROOT = Path(__file__).resolve().parent.parent
PDFKIT_SOURCE = REPO_ROOT / "helpers" / "pdf_text_extract.swift"
CACHE_DIR = Path.home() / "Library" / "Caches" / "macos-download-janitor"
PDFKIT_BINARY = CACHE_DIR / "pdf_text_extract"

LEGAL_ENTITY_RE = re.compile(
    r"(?<![\w])([A-ZÁÉÍÓÖŐÚÜŰ0-9][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9&@+.,'’()/_ -]{1,100}?"
    r"(?:Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Ltd\.?|Limited|GmbH|AG|Inc\.?|LLC|B\.V\.|S\.A\.|SAS|s\.r\.o\.|Sp\. z o\.o\.))(?=$|[\s,;:])",
    re.I,
)

SELLER_HINTS = ("eladó", "elado", "szállító", "szallito", "kibocsátó", "kibocsato", "szolgáltató", "szolgaltato", "supplier", "seller", "vendor", "issuer", "merchant", "provider", "biztosító", "biztosito", "forgalmazó", "forgalmazo")
BUYER_HINTS = ("vevő", "vevo", "buyer", "customer", "ügyfél", "ugyfel", "megrendelő", "megrendelo", "számlafizető", "szamlafizeto", "bill to", "ship to")

GENERIC_HOST_SUFFIXES = ("googleusercontent.com", "google.com", "gmail.com", "icloud.com", "apple.com", "amazonaws.com", "cloudfront.net", "office.com", "microsoft.com", "live.com")


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def key_for(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", fold(s).lower())).strip()


def ensure_pdfkit():
    if not PDFKIT_SOURCE.is_file():
        return None, "helper source missing"
    try:
        if PDFKIT_BINARY.is_file() and PDFKIT_BINARY.stat().st_mtime >= PDFKIT_SOURCE.stat().st_mtime:
            return PDFKIT_BINARY, "cached"
    except OSError:
        pass

    swiftc = shutil.which("swiftc")
    if not swiftc and shutil.which("xcrun"):
        try:
            r = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                swiftc = r.stdout.strip()
        except Exception:
            pass
    if not swiftc:
        return None, "swiftc unavailable"

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([swiftc, str(PDFKIT_SOURCE), "-framework", "PDFKit", "-o", str(PDFKIT_BINARY)], capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and PDFKIT_BINARY.is_file():
            return PDFKIT_BINARY, "compiled"
        msg = (r.stderr or r.stdout).strip().splitlines()
        return None, msg[-1][:160] if msg else "compile failed"
    except Exception as e:
        return None, str(e)[:160]


def run_pdftotext(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "-f", "1", "-l", "3", "-layout", str(path), "-"], capture_output=True, text=True, timeout=20)
        return r.stdout[:250000] if r.returncode == 0 else ""
    except Exception:
        return ""


def run_pdfkit(path, helper):
    if not helper:
        return ""
    try:
        r = subprocess.run([str(helper), str(path), "3"], capture_output=True, text=True, timeout=20)
        return r.stdout[:250000] if r.returncode == 0 else ""
    except Exception:
        return ""


def extract_text(path, helper):
    s = spotlight_text(path)
    if len(s.strip()) >= 80:
        return s, "spotlight"
    p = run_pdftotext(path)
    if len(p.strip()) > len(s.strip()):
        return p, "pdftotext"
    k = run_pdfkit(path, helper)
    if len(k.strip()) > len(s.strip()):
        return k, "pdfkit"
    return s, "spotlight" if s.strip() else "none"


def role_for(context):
    lo = context.lower()
    seller = sum(x in lo for x in SELLER_HINTS)
    buyer = sum(x in lo for x in BUYER_HINTS)
    if seller > buyer and seller:
        return "seller"
    if buyer > seller and buyer:
        return "buyer"
    return "unknown"


def entities_from(text):
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    out = {}
    for i, line in enumerate(lines[:1200]):
        context = " | ".join([lines[i-1][-180:] if i else "", line[:400], lines[i+1][:180] if i+1 < len(lines) else ""])
        role = role_for(context)
        for m in LEGAL_ENTITY_RE.finditer(line):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n:;,.-")[:120]
            key = key_for(name)
            if len(key) >= 3:
                out[(key, role)] = (name, key, role)
    return out.values()


def source_hosts(origins):
    hosts = []
    for o in origins:
        m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)", o)
        if not m:
            continue
        h = m.group(1).lower().removeprefix("www.")
        if not any(h == s or h.endswith("." + s) for s in GENERIC_HOST_SUFFIXES):
            hosts.append(h)
    return sorted(set(hosts))


def markers_for(name, hosts):
    low = name.lower().strip()
    base = re.sub(r"\s+(?:kft\.?|zrt\.?|nyrt\.?|bt\.?|kkt\.?|ltd\.?|limited|gmbh|ag|inc\.?|llc|b\.v\.|s\.a\.|sas|s\.r\.o\.|sp\. z o\.o\.)$", "", low, flags=re.I).strip()
    vals = [low, fold(low)] + ([base, fold(base)] if len(base) >= 3 else []) + hosts
    return tuple(dict.fromkeys(v for v in vals if v))[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--emit-rules", action="store_true")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    reports = root / "_Janitor" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    helper, helper_status = ensure_pdfkit()
    pdfs = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    extraction = Counter()
    known = Counter()
    docs = defaultdict(set)
    roles = defaultdict(Counter)
    hosts_by_entity = defaultdict(Counter)
    names = {}
    filenames = defaultdict(list)

    for path in pdfs:
        origins = where_froms(path)
        text, backend = extract_text(path, helper)
        extraction[backend] += 1
        haystack = "\n".join([path.name, " ".join(origins), text])
        vendor = match_vendor(haystack)
        if vendor != "Unknown":
            known[vendor] += 1
        hosts = source_hosts(origins)
        for name, key, role in entities_from(text):
            names.setdefault(key, name)
            docs[key].add(path.name)
            roles[key][role] += 1
            for h in hosts:
                hosts_by_entity[key][h] += 1
            if len(filenames[key]) < 20:
                filenames[key].append(path.name)

    rows = []
    for key, ds in docs.items():
        r = roles[key]
        conf = "high" if r["seller"] >= 2 else "medium" if r["seller"] >= 1 or len(ds) >= 3 else "low"
        rows.append((names[key], len(ds), r["seller"], r["buyer"], r["unknown"], conf, key))
    rows.sort(key=lambda x: (-x[2], -x[1], x[0].lower()))

    summary = reports / "pdf_vendor_candidates.csv"
    with summary.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["candidate", "documents", "seller_hits", "buyer_hits", "unknown_hits", "confidence"])
        for row in rows:
            w.writerow(row[:6])

    print("\nPDF VENDOR DISCOVERY v2")
    print("=" * 78)
    print(f"Top-level PDFs:       {len(pdfs)}")
    print(f"pdftotext available:  {'yes' if shutil.which('pdftotext') else 'no'}")
    print(f"PDFKit helper:        {helper_status}")
    print(f"Text via Spotlight:   {extraction['spotlight']}")
    print(f"Text via pdftotext:   {extraction['pdftotext']}")
    print(f"Text via PDFKit:      {extraction['pdfkit']}")
    print(f"No text extracted:    {extraction['none']}")
    print(f"Candidate entities:   {len(rows)}")
    print(f"Summary report:       {summary}")

    if known:
        print("\nExisting VENDOR_RULES hits:")
        for vendor, count in known.most_common():
            print(f"  {count:4}  {vendor}")

    candidates = [r for r in rows if r[1] >= ns.min_count]
    print(f"\nRecurring entity candidates (>= {ns.min_count} PDFs):")
    for name, count, seller, buyer, unknown, conf, key in candidates[:60]:
        print(f"  {count:4} docs  seller={seller:>3}  buyer={buyer:>3}  unknown={unknown:>3}  {conf:<6}  {name}")
        if ns.verbose:
            for fn in filenames[key][:8]:
                print(f"        {fn}")

    if ns.emit_rules:
        out = reports / "pdf_vendor_rule_suggestions.py"
        existing = {name for name, _ in VENDOR_RULES}
        with out.open("w", encoding="utf-8") as f:
            f.write("# LOCAL DRAFT. REVIEW MANUALLY. MAY CONTAIN PRIVATE COMPANY NAMES.\n")
            f.write("VENDOR_RULE_SUGGESTIONS = [\n")
            for name, count, seller, buyer, unknown, conf, key in candidates:
                if name in existing or seller < 1:
                    continue
                hosts = [h for h, _ in hosts_by_entity[key].most_common(4)]
                f.write(f"    # docs={count} seller={seller} buyer={buyer} confidence={conf}\n")
                f.write(f"    ({name!r}, {markers_for(name, hosts)!r}),\n")
            f.write("]\n")
        print(f"\nDraft rules:          {out}")

    print("\nREAD-ONLY AUDIT. No PDFs were moved or modified.")
    print("Nested directories were not traversed.")
    if extraction["none"] == len(pdfs) and pdfs:
        print("WARNING: no backend returned text. If PDFKit is unavailable, install poppler to provide pdftotext.")

if __name__ == "__main__":
    main()

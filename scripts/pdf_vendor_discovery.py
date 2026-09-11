#!/usr/bin/env python3

import argparse
import csv
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from downloads_organizer import DEFAULT_ROOT, VENDOR_RULES, match_vendor, where_froms, spotlight_text

LEGAL_SUFFIXES = (
    "Kft", "Kft.", "Zrt", "Zrt.", "Nyrt", "Nyrt.", "Bt", "Bt.", "Kkt", "Kkt.",
    "Ltd", "Ltd.", "Limited", "GmbH", "AG", "Inc", "Inc.", "LLC", "B.V.", "S.A.",
    "SAS", "s.r.o.", "Sp. z o.o.",
)

LEGAL_ENTITY_RE = re.compile(
    r"(?<![\w])"
    r"([A-ZÁÉÍÓÖŐÚÜŰ0-9][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9&@+.,'’()/_ -]{1,100}?"
    r"(?:Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Ltd\.?|Limited|GmbH|AG|Inc\.?|LLC|B\.V\.|S\.A\.|SAS|s\.r\.o\.|Sp\. z o\.o\.))"
    r"(?=$|[\s,;:])",
    re.I,
)

SELLER_HINTS = (
    "eladó", "elado", "szállító", "szallito", "kibocsátó", "kibocsato", "szolgáltató",
    "szolgaltato", "értékesítő", "ertekesito", "supplier", "seller", "vendor", "issuer",
    "merchant", "provider", "biztosító", "biztosito", "bank", "forgalmazó", "forgalmazo",
)
BUYER_HINTS = (
    "vevő", "vevo", "buyer", "customer", "ügyfél", "ugyfel", "megrendelő", "megrendelo",
    "számlafizető", "szamlafizeto", "bill to", "ship to",
)

GENERIC_HOST_SUFFIXES = (
    "googleusercontent.com", "google.com", "gmail.com", "icloud.com", "apple.com",
    "amazonaws.com", "cloudfront.net", "office.com", "microsoft.com", "live.com",
)
GENERIC_ENTITY_NAMES = {
    "invoice", "számla", "szamla", "bank", "customer", "buyer", "seller", "supplier",
    "magyarország kft", "magyarorszag kft",
}


def accentfold(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def clean_entity(value):
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n:;,.-")
    # Remove common labels accidentally captured before a company name.
    prefix_re = r"^(?:eladó|elado|szállító|szallito|kibocsátó|kibocsato|supplier|seller|vendor|vevő|vevo|buyer|customer)\s*[:\-]?\s*"
    value = re.sub(prefix_re, "", value, flags=re.I).strip()
    return value[:120]


def normalize_key(value):
    value = accentfold(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def text_for_pdf(path, use_pdftotext=True):
    text = spotlight_text(path)
    if len(text.strip()) >= 80 or not use_pdftotext or shutil.which("pdftotext") is None:
        return text, "spotlight" if text else "none"

    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "3", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0 and len(result.stdout.strip()) > len(text.strip()):
            return result.stdout[:250000], "pdftotext"
    except Exception:
        pass
    return text, "spotlight" if text else "none"


def role_from_context(context):
    low = context.lower()
    seller = sum(1 for hint in SELLER_HINTS if hint in low)
    buyer = sum(1 for hint in BUYER_HINTS if hint in low)
    if seller > buyer and seller:
        return "seller"
    if buyer > seller and buyer:
        return "buyer"
    return "unknown"


def extract_entities(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    found = []

    for idx, line in enumerate(lines[:1200]):
        prev_line = lines[idx - 1] if idx else ""
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        context = " | ".join([prev_line[-180:], line[:400], next_line[:180]])
        role = role_from_context(context)
        for match in LEGAL_ENTITY_RE.finditer(line):
            entity = clean_entity(match.group(1))
            key = normalize_key(entity)
            if len(key) < 3 or key in GENERIC_ENTITY_NAMES:
                continue
            found.append((entity, key, role, context[:700]))

    # Also catch a company on the line immediately after a seller/buyer label.
    for idx, line in enumerate(lines[:1200]):
        low = line.lower()
        hinted = None
        if any(h in low for h in SELLER_HINTS):
            hinted = "seller"
        elif any(h in low for h in BUYER_HINTS):
            hinted = "buyer"
        if not hinted or idx + 1 >= len(lines):
            continue
        next_line = lines[idx + 1]
        for match in LEGAL_ENTITY_RE.finditer(next_line):
            entity = clean_entity(match.group(1))
            key = normalize_key(entity)
            if len(key) >= 3 and key not in GENERIC_ENTITY_NAMES:
                found.append((entity, key, hinted, (line + " | " + next_line)[:700]))

    # Deduplicate the same entity/role within a document.
    unique = {}
    for entity, key, role, context in found:
        unique[(key, role)] = (entity, key, role, context)
    return list(unique.values())


def source_hosts(origins):
    hosts = []
    for origin in origins:
        m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)", origin)
        if not m:
            continue
        host = m.group(1).lower().removeprefix("www.")
        if any(host == suffix or host.endswith("." + suffix) for suffix in GENERIC_HOST_SUFFIXES):
            continue
        hosts.append(host)
    return sorted(set(hosts))


def company_markers(name, hosts):
    markers = []
    low = name.lower().strip()
    folded = accentfold(low)
    markers.append(low)
    if folded != low:
        markers.append(folded)

    without_suffix = re.sub(
        r"\s+(?:kft\.?|zrt\.?|nyrt\.?|bt\.?|kkt\.?|ltd\.?|limited|gmbh|ag|inc\.?|llc|b\.v\.|s\.a\.|sas|s\.r\.o\.|sp\. z o\.o\.)$",
        "", low, flags=re.I,
    ).strip()
    if len(without_suffix) >= 3:
        markers.append(without_suffix)
        folded_base = accentfold(without_suffix)
        if folded_base != without_suffix:
            markers.append(folded_base)

    markers.extend(hosts)
    # Compact unique list, longest/specific strings first.
    result = []
    for marker in sorted(set(markers), key=lambda x: (-len(x), x)):
        if marker and marker not in result:
            result.append(marker)
    return result[:8]


def known_vendor_names():
    return {name for name, _ in VENDOR_RULES}


def main():
    ap = argparse.ArgumentParser(description="Discover recurring PDF vendors locally from top-level Downloads PDFs.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--min-count", type=int, default=2, help="Minimum document count for candidate summary (default: 2)")
    ap.add_argument("--verbose", action="store_true", help="Print matching filenames; may reveal private information")
    ap.add_argument("--no-pdftotext", action="store_true", help="Do not use pdftotext fallback even if installed")
    ap.add_argument("--emit-rules", action="store_true", help="Write conservative draft VENDOR_RULES suggestions locally")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    known_counts = Counter()
    extraction_counts = Counter()
    entities = {}
    entity_docs = defaultdict(set)
    entity_roles = defaultdict(Counter)
    entity_hosts = defaultdict(Counter)
    entity_examples = defaultdict(list)
    entity_files = defaultdict(list)
    unresolved = 0

    for path in pdfs:
        origins = where_froms(path)
        text, text_source = text_for_pdf(path, use_pdftotext=not ns.no_pdftotext)
        extraction_counts[text_source] += 1
        haystack = "\n".join([path.name, " ".join(origins), text])
        known = match_vendor(haystack)
        if known != "Unknown":
            known_counts[known] += 1
        else:
            unresolved += 1

        hosts = source_hosts(origins)
        for entity, key, role, context in extract_entities(text):
            entities.setdefault(key, entity)
            entity_docs[key].add(path.name)
            entity_roles[key][role] += 1
            for host in hosts:
                entity_hosts[key][host] += 1
            if len(entity_examples[key]) < 3:
                entity_examples[key].append(context)
            if len(entity_files[key]) < 20:
                entity_files[key].append(path.name)

    summary_csv = report_dir / "pdf_vendor_candidates.csv"
    detail_csv = report_dir / "pdf_vendor_candidate_details.csv"
    rules_path = report_dir / "pdf_vendor_rule_suggestions.py"

    rows = []
    for key, doc_names in entity_docs.items():
        roles = entity_roles[key]
        hosts = entity_hosts[key]
        doc_count = len(doc_names)
        seller_hits = roles["seller"]
        buyer_hits = roles["buyer"]
        unknown_hits = roles["unknown"]
        confidence = "high" if seller_hits >= 2 else "medium" if seller_hits >= 1 or doc_count >= 3 else "low"
        rows.append({
            "candidate": entities[key],
            "documents": doc_count,
            "seller_hits": seller_hits,
            "buyer_hits": buyer_hits,
            "unknown_hits": unknown_hits,
            "confidence": confidence,
            "source_hosts": " | ".join(host for host, _ in hosts.most_common(6)),
        })
    rows.sort(key=lambda r: (-int(r["seller_hits"]), -int(r["documents"]), r["candidate"].lower()))

    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["candidate", "documents", "seller_hits", "buyer_hits", "unknown_hits", "confidence", "source_hosts"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    with open(detail_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["candidate", "filename", "roles", "source_hosts"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            key = normalize_key(row["candidate"])
            for filename in entity_files[key]:
                w.writerow({
                    "candidate": row["candidate"],
                    "filename": filename,
                    "roles": ",".join(sorted(entity_roles[key].keys())),
                    "source_hosts": row["source_hosts"],
                })

    print("\nPDF VENDOR DISCOVERY")
    print("=" * 78)
    print(f"Top-level PDFs:       {len(pdfs)}")
    print(f"Known vendor hits:    {sum(known_counts.values())}")
    print(f"Known vendor unknown: {unresolved}")
    print(f"Text via Spotlight:   {extraction_counts['spotlight']}")
    print(f"Text via pdftotext:   {extraction_counts['pdftotext']}")
    print(f"No text extracted:    {extraction_counts['none']}")
    print(f"Candidate entities:   {len(rows)}")
    print(f"Summary report:       {summary_csv}")
    print(f"Private detail:       {detail_csv}")

    if known_counts:
        print("\nExisting VENDOR_RULES hits:")
        for vendor, count in known_counts.most_common():
            print(f"  {count:4}  {vendor}")

    candidates = [r for r in rows if int(r["documents"]) >= ns.min_count]
    if candidates:
        print(f"\nRecurring entity candidates (>= {ns.min_count} PDFs):")
        for r in candidates[:60]:
            print(
                f"  {r['documents']:4} docs  seller={r['seller_hits']:>3}  buyer={r['buyer_hits']:>3}  "
                f"unknown={r['unknown_hits']:>3}  {r['confidence']:<6}  {r['candidate']}"
            )
            if ns.verbose:
                key = normalize_key(r["candidate"])
                for filename in entity_files[key][:8]:
                    print(f"        {filename}")
    else:
        print(f"\nNo entity candidate appeared in at least {ns.min_count} PDFs.")

    if ns.emit_rules:
        existing = known_vendor_names()
        emitted = []
        for r in candidates:
            if r["candidate"] in existing:
                continue
            # Only emit a draft when there is seller evidence. Repeated unknown entities
            # are deliberately not promoted because they may be the buyer/owner.
            if int(r["seller_hits"]) < 1:
                continue
            key = normalize_key(r["candidate"])
            hosts = [h for h, _ in entity_hosts[key].most_common(4)]
            emitted.append((r["candidate"], company_markers(r["candidate"], hosts), r))

        with open(rules_path, "w", encoding="utf-8") as f:
            f.write("# AUTO-GENERATED LOCAL DRAFT. REVIEW BEFORE COPYING INTO VENDOR_RULES.\n")
            f.write("# This file may contain names inferred from private PDFs; it is gitignored.\n\n")
            f.write("VENDOR_RULE_SUGGESTIONS = [\n")
            for name, markers, row in emitted:
                f.write(
                    f"    # docs={row['documents']} seller={row['seller_hits']} buyer={row['buyer_hits']} "
                    f"confidence={row['confidence']}\n"
                )
                f.write(f"    ({name!r}, {tuple(markers)!r}),\n")
            f.write("]\n")
        print(f"\nDraft rules:          {rules_path}")
        print(f"Draft rules emitted:  {len(emitted)}")

    print("\nREAD-ONLY AUDIT. No PDFs were moved or modified.")
    print("Only top-level Downloads PDFs were scanned; nested directories were not traversed.")
    if not ns.verbose:
        print("Filenames are hidden from terminal output. Use --verbose only when you want private detail shown locally.")


if __name__ == "__main__":
    main()

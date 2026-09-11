#!/usr/bin/env python3

import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from downloads_organizer import DEFAULT_ROOT, VENDOR_RULES, match_vendor, where_froms
from pdf_text import extract_pdf_text

# Long legal suffixes can be matched case-insensitively. AG is intentionally handled
# separately and case-sensitively: matching `ag` with re.I used to turn ordinary words
# such as `csomag`, `kizárólag`, `mag` and `bag` into fake companies.
LEGAL_SUFFIX_CI_RE = re.compile(
    r"\b(?:Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Ltd\.?|Limited|GmbH|Inc\.?|LLC|B\.V\.|S\.A\.|SAS|s\.r\.o\.|Sp\.?\s+z\.?\s+o\.?o\.?)\b\.?(?=$|[\s,;:)])",
    re.I,
)
LEGAL_SUFFIX_AG_RE = re.compile(r"\bAG\b(?=$|[\s,;:)])")

SELLER_HINTS = (
    "eladó", "elado", "szállító", "szallito", "kibocsátó", "kibocsato",
    "szolgáltató", "szolgaltato", "értékesítő", "ertekesito", "supplier",
    "seller", "vendor", "issuer", "merchant", "provider", "forgalmazó", "forgalmazo",
)
BUYER_HINTS = (
    "vevő", "vevo", "buyer", "customer", "ügyfél", "ugyfel", "megrendelő", "megrendelo",
    "számlafizető", "szamlafizeto", "bill to", "ship to", "receiver", "recipient", "címzett", "cimzett",
)

# These words may appear before the actual company name because PDF text extraction
# flattens tables/labels. They are stripped from the detected entity itself.
ENTITY_PREFIX_RE = re.compile(
    r"^(?:(?:eladó|elado|szállító|szallito|kibocsátó|kibocsato|szolgáltató|szolgaltato|"
    r"értékesítő|ertekesito|supplier|seller|vendor|issuer|merchant|provider|forgalmazó|forgalmazo|"
    r"vevő|vevo|buyer|customer|receiver|recipient|címzett|cimzett|bill\s+to|ship\s+to)\s*[:\-]?\s*)+",
    re.I,
)

COMPANY_CONNECTORS = {
    "&", "and", "és", "es", "und", "of", "the", "de", "del", "di", "da", "van", "von",
}
LEFT_STOPWORDS = {
    "a", "az", "egy", "the", "an", "and", "und", "der", "die", "das", "den", "dem", "im", "in",
    "to", "for", "from", "by", "at", "on", "of", "mit", "von", "am", "is", "was", "as",
    "ha", "hogy", "és", "es", "illetve", "valamint", "akkor", "mert", "majd", "mint", "csak",
    "please", "contact", "after", "before", "with", "without", "your", "our",
}
GENERIC_ENTITY_KEYS = {
    "invoice", "szamla", "bank", "customer", "buyer", "seller", "supplier", "vendor",
}
GENERIC_HOST_SUFFIXES = (
    "googleusercontent.com", "google.com", "gmail.com", "icloud.com", "apple.com",
    "amazonaws.com", "cloudfront.net", "office.com", "microsoft.com", "live.com",
)
LEGAL_WORDS = {
    "kft", "zrt", "nyrt", "bt", "kkt", "ltd", "limited", "gmbh", "ag", "inc", "llc", "bv", "sa", "sas", "sro", "sp", "zo", "oo",
}
BRAND_STOPWORDS = {
    "bank", "online", "group", "hungary", "magyarorszag", "service", "services", "szolgaltato", "biztosito",
}


def accentfold(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def normalize_key(value):
    value = accentfold(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_token(token):
    return token.strip(" \t\r\n,;:!?[]{}<>\"'“”„’`")


def clean_entity_prefix(entity):
    entity = re.sub(r"\s+", " ", entity).strip(" ,;:-")
    entity = ENTITY_PREFIX_RE.sub("", entity).strip(" ,;:-")

    # Articles are common table/paragraph glue, but only strip them if enough of the
    # entity remains to avoid damaging legitimate one-word brands.
    parts = entity.split()
    if len(parts) >= 3 and accentfold(parts[0]).lower().strip(".") in {"a", "az", "the"}:
        entity = " ".join(parts[1:])
    return entity.strip(" ,;:-")


def companyish_token(token):
    token = strip_token(token)
    if not token:
        return False
    low = accentfold(token).lower().strip("().")
    if low in COMPANY_CONNECTORS:
        return True
    if low in LEFT_STOPWORDS:
        return False
    if "&" in token or "+" in token:
        return True
    if any(ch.isupper() for ch in token):
        return True
    if any(ch.isdigit() for ch in token):
        return True
    if token.endswith(".") and len(token) <= 6:
        return True
    if "." in token and any(ch.isalpha() for ch in token):
        return True
    return False


def suffix_spans(line):
    spans = [(m.start(), m.end(), m.group(0)) for m in LEGAL_SUFFIX_CI_RE.finditer(line)]
    spans.extend((m.start(), m.end(), m.group(0)) for m in LEGAL_SUFFIX_AG_RE.finditer(line))
    return sorted(set(spans))


def entity_before_suffix(line, suffix_start, suffix_end):
    left = line[:suffix_start].rstrip()
    suffix = line[suffix_start:suffix_end].strip()
    if not left:
        return ""

    tokens = left.split()
    picked = []
    for raw in reversed(tokens[-12:]):
        token = strip_token(raw)
        low = accentfold(token).lower().strip("().")
        if not token or low in LEFT_STOPWORDS:
            break
        if companyish_token(token):
            picked.append(token)
            if len(picked) >= 9:
                break
        else:
            break

    picked.reverse()
    if not picked:
        return ""

    entity = clean_entity_prefix(" ".join([*picked, suffix]))
    key = normalize_key(entity)
    if not key or key in GENERIC_ENTITY_KEYS:
        return ""
    if len(entity.split()) < 2:
        return ""
    return entity[:140]


def role_from_context(context):
    low = context.lower()
    seller = sum(1 for hint in SELLER_HINTS if hint in low)
    buyer = sum(1 for hint in BUYER_HINTS if hint in low)
    if seller > buyer and seller:
        return "seller"
    if buyer > seller and buyer:
        return "buyer"
    return "unknown"


def canonical_entity(entity):
    """Merge already-known vendor aliases but keep unknown company names exact."""
    known = match_vendor(entity)
    if known != "Unknown":
        return known, f"known:{normalize_key(known)}", known

    # Public, generic normalization for a historical Signal company-name variant.
    folded = normalize_key(entity)
    if "signal biztosito" in folded or "signal iduna biztosito" in folded:
        return "Signal", "known:signal", "Signal"

    return entity, normalize_key(entity), ""


def extract_entities(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    found = []

    for idx, line in enumerate(lines[:1200]):
        prev_line = lines[idx - 1] if idx else ""
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        context = " | ".join([prev_line[-160:], line[:420], next_line[:160]])
        role = role_from_context(context)

        for start, end, _suffix in suffix_spans(line):
            raw_entity = entity_before_suffix(line, start, end)
            if not raw_entity:
                continue
            display, key, known = canonical_entity(raw_entity)
            if len(key) < 3:
                continue
            found.append((display, key, role, context[:700], raw_entity, known, idx))

    # Deduplicate within one document, preferring the earliest occurrence for evidence.
    unique = {}
    for item in found:
        display, key, role, *_rest, idx = item
        old = unique.get((key, role))
        if old is None or idx < old[-1]:
            unique[(key, role)] = item
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


def brand_tokens(entity):
    tokens = normalize_key(entity).split()
    return [
        t for t in tokens
        if len(t) >= 3 and t not in LEGAL_WORDS and t not in BRAND_STOPWORDS
    ]


def source_matches_entity(entity, hosts):
    if not hosts:
        return False
    tokens = brand_tokens(entity)
    if not tokens:
        return False
    host_blob = " ".join(accentfold(h).lower() for h in hosts)
    return any(token in host_blob for token in tokens[:4])


def company_markers(name, hosts):
    markers = []
    low = name.lower().strip()
    folded = accentfold(low)
    markers.extend([low, folded])

    without_suffix = re.sub(
        r"\s+(?:kft\.?|zrt\.?|nyrt\.?|bt\.?|kkt\.?|ltd\.?|limited|gmbh|ag|inc\.?|llc|b\.v\.|s\.a\.|sas|s\.r\.o\.|sp\. z o\.o\.)$",
        "", low, flags=re.I,
    ).strip()
    if len(without_suffix) >= 3:
        markers.extend([without_suffix, accentfold(without_suffix)])
    markers.extend(hosts)

    result = []
    for marker in sorted(set(markers), key=lambda x: (-len(x), x)):
        if marker and marker not in result:
            result.append(marker)
    return result[:8]


def main():
    ap = argparse.ArgumentParser(description="Discover recurring PDF vendors locally from top-level Downloads PDFs.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--min-count", type=int, default=2, help="Minimum PDF count for recurring candidates")
    ap.add_argument("--verbose", action="store_true", help="Show matching filenames; may reveal private information")
    ap.add_argument("--show-mentions", action="store_true", help="Also print recurring company mentions without vendor evidence")
    ap.add_argument("--no-pdftotext", action="store_true", help="Disable pdftotext fallback")
    ap.add_argument("--no-pdfkit", action="store_true", help="Disable native macOS PDFKit fallback")
    ap.add_argument("--emit-rules", action="store_true", help="Write conservative local rule suggestions")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    known_counts = Counter()
    extraction_counts = Counter()
    diagnostics_counts = defaultdict(Counter)
    entities = {}
    entity_docs = defaultdict(set)
    entity_roles = defaultdict(Counter)
    entity_hosts = defaultdict(Counter)
    entity_files = defaultdict(list)
    raw_variants = defaultdict(Counter)
    entity_known_vendor = {}
    entity_header_docs = defaultdict(set)
    entity_source_docs = defaultdict(set)

    for path in pdfs:
        origins = where_froms(path)
        text, text_source, diagnostics = extract_pdf_text(
            path,
            max_pages=3,
            allow_pdftotext=not ns.no_pdftotext,
            allow_pdfkit=not ns.no_pdfkit,
        )
        extraction_counts[text_source] += 1
        for backend, status in diagnostics.items():
            diagnostics_counts[backend][status] += 1

        haystack = "\n".join([path.name, " ".join(origins), text])
        known = match_vendor(haystack)
        if known != "Unknown":
            known_counts[known] += 1

        hosts = source_hosts(origins)
        for display, key, role, _context, raw_entity, known_vendor, line_idx in extract_entities(text):
            entities.setdefault(key, display)
            entity_docs[key].add(path.name)
            entity_roles[key][role] += 1
            raw_variants[key][raw_entity] += 1
            if known_vendor:
                entity_known_vendor[key] = known_vendor
            if line_idx < 25:
                entity_header_docs[key].add(path.name)
            if source_matches_entity(display, hosts):
                entity_source_docs[key].add(path.name)
            for host in hosts:
                entity_hosts[key][host] += 1
            if len(entity_files[key]) < 20:
                entity_files[key].append(path.name)

    summary_csv = report_dir / "pdf_vendor_candidates.csv"
    detail_csv = report_dir / "pdf_vendor_candidate_details.csv"
    rules_path = report_dir / "pdf_vendor_rule_suggestions.py"

    rows = []
    for key, doc_names in entity_docs.items():
        roles = entity_roles[key]
        doc_count = len(doc_names)
        seller_hits = roles["seller"]
        buyer_hits = roles["buyer"]
        unknown_hits = roles["unknown"]
        known_vendor = entity_known_vendor.get(key, "")
        header_hits = len(entity_header_docs[key])
        source_hits = len(entity_source_docs[key])

        if known_vendor:
            confidence = "known"
        elif seller_hits >= 2 and seller_hits > buyer_hits:
            confidence = "high"
        elif source_hits >= 2 and buyer_hits == 0:
            confidence = "high"
        elif seller_hits >= 1 and seller_hits >= buyer_hits:
            confidence = "medium"
        elif source_hits >= 1 and buyer_hits == 0:
            confidence = "medium"
        else:
            confidence = "mention"

        rows.append({
            "candidate": entities[key],
            "documents": doc_count,
            "seller_hits": seller_hits,
            "buyer_hits": buyer_hits,
            "unknown_hits": unknown_hits,
            "header_hits": header_hits,
            "source_hits": source_hits,
            "confidence": confidence,
            "known_vendor": known_vendor,
            "source_hosts": " | ".join(host for host, _ in entity_hosts[key].most_common(6)),
            "raw_variants": " | ".join(name for name, _ in raw_variants[key].most_common(6)),
        })

    rank = {"known": 0, "high": 1, "medium": 2, "mention": 3}
    rows.sort(key=lambda r: (rank.get(r["confidence"], 9), -int(r["source_hits"]), -int(r["seller_hits"]), -int(r["documents"]), r["candidate"].lower()))

    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = [
            "candidate", "documents", "seller_hits", "buyer_hits", "unknown_hits",
            "header_hits", "source_hits", "confidence", "known_vendor", "source_hosts", "raw_variants",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    with open(detail_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["candidate", "filename", "roles", "header_evidence", "source_evidence", "source_hosts"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            key = f"known:{normalize_key(row['known_vendor'])}" if row["known_vendor"] else normalize_key(row["candidate"])
            for filename in entity_files[key]:
                w.writerow({
                    "candidate": row["candidate"],
                    "filename": filename,
                    "roles": ",".join(sorted(entity_roles[key].keys())),
                    "header_evidence": "yes" if filename in entity_header_docs[key] else "no",
                    "source_evidence": "yes" if filename in entity_source_docs[key] else "no",
                    "source_hosts": row["source_hosts"],
                })

    print("\nPDF VENDOR DISCOVERY")
    print("=" * 78)
    print(f"Top-level PDFs:       {len(pdfs)}")
    print(f"Known vendor hits:    {sum(known_counts.values())}")
    print(f"Text via Spotlight:   {extraction_counts['spotlight']}")
    print(f"Text via pdftotext:   {extraction_counts['pdftotext']}")
    print(f"Text via PDFKit:      {extraction_counts['pdfkit']}")
    print(f"No text extracted:    {extraction_counts['none']}")
    print(f"Candidate entities:   {len(rows)}")
    print(f"Summary report:       {summary_csv}")
    print(f"Private detail:       {detail_csv}")

    if diagnostics_counts["pdfkit"]:
        status, count = diagnostics_counts["pdfkit"].most_common(1)[0]
        print(f"PDFKit helper:        {status} ({count} PDFs)")
    if diagnostics_counts["pdftotext"]:
        status, count = diagnostics_counts["pdftotext"].most_common(1)[0]
        print(f"pdftotext:            {status} ({count} PDFs)")

    if known_counts:
        print("\nExisting VENDOR_RULES hits:")
        for vendor, count in known_counts.most_common():
            print(f"  {count:4}  {vendor}")

    recurring = [r for r in rows if int(r["documents"]) >= ns.min_count]
    vendor_candidates = [r for r in recurring if r["confidence"] != "mention"]
    mentions = [r for r in recurring if r["confidence"] == "mention"]

    if vendor_candidates:
        print(f"\nProbable vendor candidates (>= {ns.min_count} PDFs):")
        for r in vendor_candidates[:80]:
            known_tag = " [known]" if r["known_vendor"] else ""
            print(
                f"  {r['documents']:4} docs  seller={r['seller_hits']:>3}  source={r['source_hits']:>3}  "
                f"header={r['header_hits']:>3}  buyer={r['buyer_hits']:>3}  {r['confidence']:<7}  {r['candidate']}{known_tag}"
            )
            if ns.verbose:
                key = f"known:{normalize_key(r['known_vendor'])}" if r["known_vendor"] else normalize_key(r["candidate"])
                for filename in entity_files[key][:8]:
                    print(f"        {filename}")
    else:
        print(f"\nNo probable vendor candidate appeared in at least {ns.min_count} PDFs.")

    if mentions:
        if ns.show_mentions:
            print(f"\nRecurring company mentions without vendor evidence (>= {ns.min_count} PDFs):")
            for r in mentions[:80]:
                print(
                    f"  {r['documents']:4} docs  header={r['header_hits']:>3}  buyer={r['buyer_hits']:>3}  "
                    f"mention  {r['candidate']}"
                )
        else:
            print(f"\nRecurring non-vendor company mentions hidden: {len(mentions)} (use --show-mentions to inspect)")

    if ns.emit_rules:
        emitted = []
        for r in vendor_candidates:
            if r["known_vendor"] or r["confidence"] not in {"high", "medium"}:
                continue
            # Learning requires positive vendor evidence and no buyer-side evidence.
            if int(r["buyer_hits"]) > 0:
                continue
            if int(r["seller_hits"]) < 1 and int(r["source_hits"]) < 1:
                continue
            key = normalize_key(r["candidate"])
            hosts = [h for h, _ in entity_hosts[key].most_common(4)]
            emitted.append((r["candidate"], company_markers(r["candidate"], hosts), r))

        with open(rules_path, "w", encoding="utf-8") as f:
            f.write("# AUTO-GENERATED LOCAL DRAFT. REVIEW BEFORE COPYING INTO VENDOR_RULES.\n")
            f.write("# May contain company names inferred from private PDFs; this path is gitignored.\n\n")
            f.write("VENDOR_RULE_SUGGESTIONS = [\n")
            for name, markers, row in emitted:
                f.write(
                    f"    # docs={row['documents']} seller={row['seller_hits']} source={row['source_hits']} "
                    f"buyer={row['buyer_hits']} confidence={row['confidence']}\n"
                )
                f.write(f"    ({name!r}, {tuple(markers)!r}),\n")
            f.write("]\n")
        print(f"\nDraft rules:          {rules_path}")
        print(f"Draft rules emitted:  {len(emitted)}")

    print("\nREAD-ONLY AUDIT. No PDFs were moved or modified.")
    print("Only top-level Downloads PDFs were scanned; nested directories were not traversed.")
    if extraction_counts['none'] == len(pdfs) and pdfs:
        print("WARNING: no PDF text backend returned text. Check PDFKit/pdftotext diagnostics above.")
    if not ns.verbose:
        print("Filenames are hidden from terminal output. Use --verbose only when you want private detail shown locally.")


if __name__ == "__main__":
    main()

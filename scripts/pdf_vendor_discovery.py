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
    "számlafizető", "szamlafizeto", "bill to", "ship to",
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


def accentfold(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def normalize_key(value):
    value = accentfold(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_token(token):
    return token.strip(" \t\r\n,;:!?[]{}<>\"'“”„’`")


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
    # Short business abbreviations are often lowercase after PDF text extraction.
    if token.endswith(".") and len(token) <= 6:
        return True
    # Domain-like brand names such as alza.hu.
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

    entity = " ".join([*picked, suffix])
    entity = re.sub(r"\s+", " ", entity).strip(" ,;:-")
    key = normalize_key(entity)
    if not key or key in GENERIC_ENTITY_KEYS:
        return ""

    # A legal entity should have something meaningful before the suffix.
    if len(picked) == 1 and len(normalize_key(picked[0])) < 2:
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
            found.append((display, key, role, context[:700], raw_entity, known))

    # Deduplicate the same entity/role within one document.
    unique = {}
    for item in found:
        display, key, role, *_ = item
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
        for display, key, role, _context, raw_entity, known_vendor in extract_entities(text):
            entities.setdefault(key, display)
            entity_docs[key].add(path.name)
            entity_roles[key][role] += 1
            raw_variants[key][raw_entity] += 1
            if known_vendor:
                entity_known_vendor[key] = known_vendor
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

        if known_vendor:
            confidence = "known"
        elif seller_hits >= 2 and seller_hits > buyer_hits:
            confidence = "high"
        elif seller_hits >= 1 and seller_hits >= buyer_hits:
            confidence = "medium"
        elif doc_count >= 3 and buyer_hits == 0:
            confidence = "medium"
        else:
            confidence = "low"

        rows.append({
            "candidate": entities[key],
            "documents": doc_count,
            "seller_hits": seller_hits,
            "buyer_hits": buyer_hits,
            "unknown_hits": unknown_hits,
            "confidence": confidence,
            "known_vendor": known_vendor,
            "source_hosts": " | ".join(host for host, _ in entity_hosts[key].most_common(6)),
            "raw_variants": " | ".join(name for name, _ in raw_variants[key].most_common(6)),
        })

    rank = {"known": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: (rank.get(r["confidence"], 9), -int(r["seller_hits"]), -int(r["documents"]), r["candidate"].lower()))

    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["candidate", "documents", "seller_hits", "buyer_hits", "unknown_hits", "confidence", "known_vendor", "source_hosts", "raw_variants"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    with open(detail_csv, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["candidate", "filename", "roles", "source_hosts"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            key = f"known:{normalize_key(row['known_vendor'])}" if row["known_vendor"] else normalize_key(row["candidate"])
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
    if recurring:
        print(f"\nRecurring entity candidates (>= {ns.min_count} PDFs):")
        for r in recurring[:80]:
            known_tag = " [known]" if r["known_vendor"] else ""
            print(
                f"  {r['documents']:4} docs  seller={r['seller_hits']:>3}  buyer={r['buyer_hits']:>3}  "
                f"unknown={r['unknown_hits']:>3}  {r['confidence']:<6}  {r['candidate']}{known_tag}"
            )
            if ns.verbose:
                key = f"known:{normalize_key(r['known_vendor'])}" if r["known_vendor"] else normalize_key(r["candidate"])
                for filename in entity_files[key][:8]:
                    print(f"        {filename}")
    else:
        print(f"\nNo entity candidate appeared in at least {ns.min_count} PDFs.")

    if ns.emit_rules:
        emitted = []
        for r in recurring:
            if r["known_vendor"]:
                continue
            # Do not learn a rule from buyer-side occurrences or mere narrative mentions.
            if int(r["seller_hits"]) < 1 or int(r["buyer_hits"]) > 0:
                continue
            key = normalize_key(r["candidate"])
            hosts = [h for h, _ in entity_hosts[key].most_common(4)]
            emitted.append((r["candidate"], company_markers(r["candidate"], hosts), r))

        with open(rules_path, "w", encoding="utf-8") as f:
            f.write("# AUTO-GENERATED LOCAL DRAFT. REVIEW BEFORE COPYING INTO VENDOR_RULES.\n")
            f.write("# May contain company names inferred from private PDFs; this path is gitignored.\n\n")
            f.write("VENDOR_RULE_SUGGESTIONS = [\n")
            for name, markers, row in emitted:
                f.write(f"    # docs={row['documents']} seller={row['seller_hits']} buyer={row['buyer_hits']} confidence={row['confidence']}\n")
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

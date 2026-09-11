#!/usr/bin/env python3

import argparse
import csv
import re
import unicodedata
from pathlib import Path

from downloads_organizer import DEFAULT_ROOT
from vendor_rules import load_local_vendor_rules, save_local_vendor_rules


def accentfold(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def company_markers(name, source_hosts):
    low = name.lower().strip()
    folded = accentfold(low)
    markers = [low]
    if folded != low:
        markers.append(folded)

    base = re.sub(
        r"\s+(?:kft\.?|zrt\.?|nyrt\.?|bt\.?|kkt\.?|ltd\.?|limited|gmbh|ag|inc\.?|llc|b\.v\.|s\.a\.|sas|s\.r\.o\.|sp\. z o\.o\.)$",
        "", low, flags=re.I,
    ).strip()
    if len(base) >= 3:
        markers.append(base)
        base_folded = accentfold(base)
        if base_folded != base:
            markers.append(base_folded)

    for host in source_hosts:
        host = host.strip().lower()
        if host:
            markers.append(host)

    result = []
    seen = set()
    for marker in sorted(markers, key=lambda m: (-len(m), m)):
        key = marker.lower()
        if marker and key not in seen:
            result.append(marker)
            seen.add(key)
    return result[:8]


def load_candidates(path, include_medium=False):
    if not path.is_file():
        raise SystemExit(f"Missing discovery report: {path}\nRun: ./janitor pdf vendors --emit-rules")

    selected = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            confidence = (row.get("confidence") or "").strip().lower()
            known_vendor = (row.get("known_vendor") or "").strip()
            seller_hits = int(row.get("seller_hits") or 0)
            source_hits = int(row.get("source_hits") or 0)
            buyer_hits = int(row.get("buyer_hits") or 0)
            docs = int(row.get("documents") or 0)

            if known_vendor:
                continue
            if confidence == "high":
                pass
            elif include_medium and confidence == "medium":
                pass
            else:
                continue
            if buyer_hits > 0:
                continue
            if seller_hits < 1 and source_hits < 1:
                continue

            name = (row.get("candidate") or "").strip()
            if not name:
                continue
            hosts = [h.strip() for h in (row.get("source_hosts") or "").split("|") if h.strip()]
            selected.append({
                "name": name,
                "markers": company_markers(name, hosts),
                "documents": docs,
                "seller_hits": seller_hits,
                "source_hits": source_hits,
                "confidence": confidence,
            })

    selected.sort(key=lambda r: (-r["documents"], r["name"].lower()))
    return selected


def main():
    ap = argparse.ArgumentParser(description="Promote discovered PDF vendors into a private local rule file.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--include-medium", action="store_true", help="Also include medium-confidence candidates")
    ap.add_argument("--apply", action="store_true", help="Write selected rules into private local config")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation with --apply")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    report = root / "_Janitor" / "reports" / "pdf_vendor_candidates.csv"
    selected = load_candidates(report, include_medium=ns.include_medium)
    existing = {name.lower() for name, _ in load_local_vendor_rules(root)}
    new_rules = [r for r in selected if r["name"].lower() not in existing]

    print("\nPDF VENDOR RULE PROMOTION")
    print("=" * 78)
    print(f"Discovery report:     {report}")
    print(f"Eligible candidates:  {len(selected)}")
    print(f"Already local:        {len(selected) - len(new_rules)}")
    print(f"New private rules:    {len(new_rules)}")

    for row in new_rules:
        print(
            f"  {row['name']}  docs={row['documents']} seller={row['seller_hits']} "
            f"source={row['source_hits']} confidence={row['confidence']}"
        )

    if not ns.apply:
        print("\nDRY RUN ONLY. Nothing was written.")
        print("Use --apply after reviewing the candidate names above.")
        return

    if not new_rules:
        print("\nNothing new to install.")
        return

    if not ns.yes:
        answer = input(f"\nInstall {len(new_rules)} rules into private local config? [yes/NO] ")
        if answer != "yes":
            print("Aborted.")
            return

    path = save_local_vendor_rules(
        root,
        [(r["name"], r["markers"]) for r in new_rules],
        metadata={"source": "pdf_vendor_candidates.csv", "review_required": True},
    )
    print(f"\nInstalled {len(new_rules)} private local vendor rules.")
    print(f"Local config: {path}")
    print("This file is under _Janitor and is not intended for GitHub.")


if __name__ == "__main__":
    main()

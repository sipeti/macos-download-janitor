#!/usr/bin/env python3

"""Audit/reclassify PDFs already under ~/Downloads/_PDF.

This is intentionally separate from the generic Downloads organizer. It may recurse
inside _PDF because the user explicitly selected the managed PDF tree, but it never
traverses any other Downloads subdirectory.
"""

import argparse
import csv
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from downloads_organizer import (
    DEFAULT_ROOT,
    classify_pdf,
    collision_safe,
    gpt_provenance,
    human_size,
    sensitive_reason,
    where_froms,
)
from vendor_rules import (
    all_vendor_rules,
    load_local_vendor_rules_with_rejections,
)


STRONG_KIND_MARKERS = {
    "Statements": {
        "bankszámlakivonat", "bankszamlakivonat", "számlakivonat", "szamlakivonat",
        "account statement", "bank statement", "nyitó egyenleg", "nyito egyenleg",
        "záró egyenleg", "zaro egyenleg",
    },
    "Invoices": {
        "számla sorszáma", "szamla sorszama", "invoice number",
        "teljesítés dátuma", "teljesites datuma",
    },
    "Insurance": {
        "biztosítási kötvény", "biztositasi kotveny", "kötvényszám", "kotvenyszam",
        "insurance policy", "díjértesítő", "dijertesito",
    },
    "Tax": {
        "adófolyószámla", "adofolyoszamla", "adóbevallás", "adobevallas",
        "nav.gov.hu", "nemzeti adó- és vámhivatal", "nemzeti ado- es vamhivatal",
    },
    "Contracts": {
        "szerződés", "szerzodes", "megállapodás", "megallapodas",
    },
}


def iter_managed_pdfs(root):
    managed = root / "_PDF"
    if not managed.is_dir():
        return []
    return sorted(
        p for p in managed.rglob("*.pdf")
        if p.is_file() and not p.name.startswith(".")
    )


def reason_hits(reason, kind):
    prefix = f"{kind}: "
    for part in reason.split("; "):
        if part.startswith(prefix):
            return [hit.strip().lower() for hit in part[len(prefix):].split(",") if hit.strip()]
    return []


def semantic_evidence_is_strong(reason, kind):
    """Require an explicit kind-specific anchor before managed-tree auto moves.

    Generic words such as `számla`, `invoice`, `fizetendő`, `contract`, or
    `biztosítás` are useful for review ranking but are not sufficient on their own for
    automatic reclassification. This prevents things like bank/tax documents from
    becoming invoices just because those words occur somewhere in the PDF.
    """
    hits = reason_hits(reason, kind)
    strong = {marker.lower() for marker in STRONG_KIND_MARKERS.get(kind, set())}
    return any(hit in strong for hit in hits)


def managed_decision(path, root, kind, vendor, confidence, reason, dest_dir, text_backend):
    """Return (action, desired_parent, effective_confidence, reason)."""
    effective_confidence = confidence
    effective_reason = reason
    strong_kind = semantic_evidence_is_strong(reason, kind)

    # Even a generic classifier "high" result is not auto-moved inside the existing
    # archive unless it contains a strong semantic anchor for the selected kind.
    # The managed tree is historical data, so false positives are more expensive than
    # leaving a file in review.
    if confidence == "high" and kind != "Other" and not strong_kind:
        effective_confidence = "review-weak-kind"
        review_vendor = vendor if vendor != "Unknown" else "Unknown"
        dest_dir = Path("_PDF") / "_Review" / review_vendor
        effective_reason = f"{reason}; downgraded:no-strong-kind-anchor"

    # A known vendor plus one strong semantic anchor is useful enough to promote a
    # medium result in the explicitly selected managed tree.
    elif (
        confidence == "medium"
        and vendor != "Unknown"
        and kind != "Other"
        and strong_kind
    ):
        effective_confidence = "high+vendor-kind"
        dest_dir = Path("_PDF") / kind / vendor
        effective_reason = f"{reason}; promoted:known-vendor+strong-kind"

    desired_parent = root / dest_dir
    if path.parent.resolve() == desired_parent.resolve():
        action = "LEAVE"
    elif effective_confidence.startswith("high"):
        action = "MOVE"
    else:
        action = "REVIEW"

    return action, desired_parent, effective_confidence, effective_reason


def scan(root):
    vendor_rules = all_vendor_rules(root)
    rows = []

    for path in iter_managed_pdfs(root):
        rel = path.relative_to(root)
        origins = where_froms(path)
        provenance = gpt_provenance(path, origins)
        sensitive = sensitive_reason(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        if sensitive:
            rows.append({
                "path": str(rel),
                "size_bytes": size,
                "size": human_size(size),
                "action": "SENSITIVE",
                "destination": "",
                "pdf_kind": "",
                "vendor": "",
                "confidence": "blocked",
                "text_backend": "",
                "provenance": provenance,
                "reason": sensitive,
            })
            continue

        kind, vendor, confidence, reason, dest_dir, text_backend = classify_pdf(path, origins, vendor_rules)
        action, desired_parent, confidence, reason = managed_decision(
            path, root, kind, vendor, confidence, reason, dest_dir, text_backend
        )
        desired = desired_parent / path.name

        rows.append({
            "path": str(rel),
            "size_bytes": size,
            "size": human_size(size),
            "action": action,
            "destination": str(desired.relative_to(root)),
            "pdf_kind": kind,
            "vendor": vendor,
            "confidence": confidence,
            "text_backend": text_backend,
            "provenance": provenance,
            "reason": reason,
        })

    order = {"SENSITIVE": 0, "REVIEW": 1, "MOVE": 2, "LEAVE": 3}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -int(r["size_bytes"]), r["path"].lower()))
    return rows


def redacted_row(row):
    result = dict(row)
    result["path"] = "[REDACTED]"
    if result["destination"]:
        result["destination"] = str(Path(result["destination"]).parent / "[REDACTED]")
    return result


def print_summary(rows, root, report):
    counts = Counter(r["action"] for r in rows)
    bytes_by_action = Counter()
    destinations = defaultdict(lambda: [0, 0])
    for row in rows:
        bytes_by_action[row["action"]] += int(row["size_bytes"])
        if row["destination"] and row["action"] in {"MOVE", "REVIEW"}:
            folder = str(Path(row["destination"]).parent)
            destinations[folder][0] += 1
            destinations[folder][1] += int(row["size_bytes"])

    active_rules, rejected_rules = load_local_vendor_rules_with_rejections(root)
    promoted = sum(1 for r in rows if r["confidence"] == "high+vendor-kind")
    downgraded = sum(1 for r in rows if r["confidence"] == "review-weak-kind")

    print("\nPDF MANAGED TREE AUDIT")
    print("=" * 78)
    print(f"Managed tree:        {root / '_PDF'}")
    print("Scope:               recursive inside _PDF only")
    print(f"PDFs scanned:        {len(rows)}")
    print(f"Already in place:    {counts['LEAVE']}")
    print(f"Safe reclassify:     {counts['MOVE']} ({human_size(bytes_by_action['MOVE'])})")
    print(f"Review candidates:   {counts['REVIEW']} ({human_size(bytes_by_action['REVIEW'])})")
    print(f"Sensitive blocked:   {counts['SENSITIVE']}")
    print(f"Promoted vendor+kind:{promoted:5}")
    print(f"Weak-high downgraded:{downgraded:5}")
    print(f"Local vendor rules:  {len(active_rules)} active, {len(rejected_rules)} rejected")
    print(f"Local report:        {report}")

    if rejected_rules:
        print("WARNING: malformed previously learned vendor rules are being ignored.")

    if destinations:
        print("\nDestination summary:")
        for folder, (count, total) in sorted(destinations.items(), key=lambda x: (-x[1][1], x[0])):
            print(f"  {count:5}  {human_size(total):>10}  {folder}")


def main():
    ap = argparse.ArgumentParser(description="Audit or reclassify PDFs already under ~/Downloads/_PDF.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="Downloads root; managed tree is <root>/_PDF")
    ap.add_argument("--apply", action="store_true", help="Move high-confidence misclassified PDFs")
    ap.add_argument("--include-review", action="store_true", help="Also move medium/low confidence PDFs into _PDF/_Review")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="Show filenames and detailed decisions")
    ap.add_argument("--redact-report", action="store_true", help="Redact filenames in CSV report")
    ns = ap.parse_args()

    root = Path(ns.root).expanduser().resolve()
    managed = root / "_PDF"
    if not managed.is_dir():
        raise SystemExit(f"Managed PDF folder not found: {managed}")

    report_dir = root / "_Janitor" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_redacted" if ns.redact_report else ""
    report = report_dir / f"pdf_managed_plan{suffix}.csv"

    rows = scan(root)
    fields = [
        "path", "size_bytes", "size", "action", "destination", "pdf_kind", "vendor",
        "confidence", "text_backend", "provenance", "reason",
    ]
    with report.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(redacted_row(r) if ns.redact_report else r for r in rows)

    print_summary(rows, root, report)

    if ns.verbose:
        print("\nDetailed decisions:")
        for row in rows:
            meta = f"[{row['pdf_kind']}/{row['vendor']}/{row['confidence']}/{row['text_backend'] or 'no-text'}]"
            if row["action"] == "SENSITIVE":
                print(f"SENSITIVE {row['size']:>10}  {row['path']} [{row['reason']}] - no move")
            elif row["action"] == "LEAVE":
                print(f"LEAVE     {row['size']:>10}  {row['path']} {meta}")
            else:
                print(f"{row['action']:<9} {row['size']:>10}  {row['path']} -> {row['destination']} {meta}")

    if not ns.apply:
        print("\nDRY RUN ONLY. No PDFs were moved.")
        print("No folder outside _PDF was traversed or modified.")
        return

    moves = [r for r in rows if r["action"] == "MOVE"]
    if ns.include_review:
        moves += [r for r in rows if r["action"] == "REVIEW"]

    if not moves:
        print("\nNo eligible PDF moves at the current confidence threshold. Nothing was changed.")
        return

    if not ns.yes:
        review_note = " including review staging" if ns.include_review else ""
        answer = input(f"\nReclassify {len(moves)} PDFs inside _PDF{review_note}? [yes/NO] ")
        if answer != "yes":
            print("Aborted.")
            return

    moved = 0
    skipped = 0
    for row in moves:
        src = root / row["path"]
        if not src.is_file() or not row["destination"]:
            skipped += 1
            continue
        dst = root / row["destination"]
        if src.resolve() == dst.resolve():
            skipped += 1
            continue
        dst = collision_safe(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1

    # Remove only directories made empty by our moves, and only under _PDF. Never
    # remove _PDF itself. This is cosmetic; failures are harmless.
    for directory in sorted((p for p in managed.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass

    print(f"\nMoved {moved} PDFs inside _PDF; skipped {skipped}. Nothing was deleted.")
    print("Sensitive candidates were untouched, and no folder outside _PDF was traversed.")


if __name__ == "__main__":
    main()

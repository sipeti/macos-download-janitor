# Managed `_PDF` scope

The generic Downloads organizer stays top-level-only. PDF commands have a separate explicit `managed` scope for working inside the already-managed `~/Downloads/_PDF` tree.

## Read-only audit

```bash
./janitor pdf audit --scope managed
```

This recursively scans **only** `~/Downloads/_PDF` and proposes semantic/vendor destinations. No other Downloads subdirectory is traversed.

Use `--verbose` only when private filenames should be shown locally. `--redact-report` creates a filename-redacted CSV report.

## Reclassify existing `_PDF` content

```bash
./janitor pdf organize --scope managed
```

Dry-run by default. To apply only high-confidence reclassification moves:

```bash
./janitor pdf organize --scope managed --apply
```

Medium/low-confidence PDFs remain review candidates unless deliberately included:

```bash
./janitor pdf organize --scope managed --apply --include-review
```

The managed organizer:

- moves files only inside `_PDF`;
- does not delete PDFs;
- leaves already-correct files in place;
- blocks sensitive-looking filenames;
- uses the same PDFKit/pdftotext extraction and private local vendor rules as the inbox classifier;
- removes only empty directories left behind by its own moves.

## Vendor discovery on `_PDF`

```bash
./janitor pdf vendors --scope managed --emit-rules
```

This recursively analyzes only `_PDF` and writes separate local reports:

```text
~/Downloads/_Janitor/reports/pdf_vendor_candidates_managed.csv
~/Downloads/_Janitor/reports/pdf_vendor_candidate_details_managed.csv
~/Downloads/_Janitor/reports/pdf_vendor_rule_suggestions_managed.py
```

Learn from that managed-tree discovery report with:

```bash
./janitor pdf learn --scope managed
./janitor pdf learn --scope managed --apply
```

Learned rules continue to live only in the private local config:

```text
~/Downloads/_Janitor/config/vendor_rules.json
```

They are not written into the public repository.

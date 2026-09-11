# macOS Download Janitor

A conservative macOS `~/Downloads` cleanup toolkit.

Core rule:

> detect -> classify -> verify -> review -> move -> delete manually later

The project is intentionally report-first. Commands that can move files default to dry-run and require `--apply`.

## Privacy-first defaults

The organizer is deliberately conservative because Downloads may contain confidential documents, credentials, legal material, banking files, source URLs and other private information.

- Only files directly in `~/Downloads` are scanned by the generic organizer.
- Existing subdirectories are **not traversed or rearranged at all** by that organizer.
- Filenames are not printed by default; use `--verbose` to show detailed decisions.
- CSV reports can be redacted with `--redact-report`.
- Sensitive-looking filenames are blocked from automatic moves.
- Lower-confidence PDFs and probable GPT files are review candidates, not automatic moves.
- No user reports, downloaded documents or media belong in this source repository; `.gitignore` blocks common generated/private artefacts.

## Unified CLI

The repository has a single entry point:

```bash
./janitor --help
```

Recommended first pass:

```bash
./janitor scan
./janitor sensitive
./janitor gpt scan
./janitor zip report
./janitor duplicates
./janitor pdf audit
./janitor media audit
./janitor dmg audit
./janitor junk
```

Nothing above moves source files.

For details only when you actually want filenames on screen:

```bash
./janitor scan --verbose
./janitor pdf audit --verbose
```

For a share-safe report:

```bash
./janitor scan --redact-report
```

When the reports look good:

```bash
./janitor organize --apply
```

or apply only one area:

```bash
./janitor pdf organize --apply
./janitor media organize --apply
./janitor dmg organize --apply
./janitor gpt organize --apply
./janitor zip pair --apply
```

Review candidates are not moved by normal `--apply`. To stage them deliberately:

```bash
./janitor pdf organize --apply --include-review
./janitor gpt organize --apply --include-review
```

## Logical sorting order

The generic sorter uses this priority for direct children of Downloads:

1. **Sensitive filename guard**: credential/recovery/TOTP/private-key/identity-document style names are never auto-moved.
2. **Junk review**: zero-byte files and Office `~$...` lock files are review candidates.
3. **Origin first**: confirmed ChatGPT/OpenAI download metadata takes precedence over extension sorting.
4. **Probable GPT**: recognizable ChatGPT-style filenames without provenance metadata go to review, not automatic move.
5. **PDF meaning**: PDFs are classified using filename, origin URL and Spotlight-indexed text; only high-confidence results auto-move.
6. **Archive/install media**: ZIP, DMG, PKG/MPKG are separated into archive/install areas.
7. **Downloaded media by normalized source**: MP4/MOV/images/audio are grouped by sources such as YouTube, Facebook, Instagram, TikTok, Google Drive or WeTransfer.
8. **Generic documents/code**: top-level office files, CSVs and code/text files get a basic category folder.

Existing managed directories such as `_Janitor`, `fromGPT`, `_ZIP`, `_PDF`, `_DMG`, `_Media` and `_Documents` are not touched by the top-level organizer.

## Nested folders

The generic organizer intentionally uses `Path.iterdir()` and looks only at direct files in the Downloads root.

It does **not** descend into ordinary subdirectories. Existing project trees, extracted applications, package contents, `node_modules`, APK trees, media project folders and similar content are outside the scope of the first-pass sorter.

Archive-specific tools such as ZIP extraction verification may inspect nested archive relationships for their own read-only analysis, but the generic sorter will not rearrange them.

## Resulting layout

A typical Downloads tree becomes:

```text
~/Downloads/
├── fromGPT/
│   ├── images/
│   ├── video/
│   ├── audio/
│   ├── pdf/
│   ├── zips/
│   ├── csv/
│   ├── documents/
│   ├── code/
│   ├── other/
│   └── _Review/
├── _PDF/
│   ├── Statements/
│   ├── Invoices/
│   ├── Insurance/
│   ├── Tax/
│   ├── Contracts/
│   └── _Review/
│       ├── Raiffeisen/
│       ├── Signal/
│       └── Unknown/
├── _ZIP/
├── _DMG/
├── _Installers/
├── _Media/
│   ├── YouTube/video/
│   ├── Facebook/video/
│   ├── Instagram/images/
│   ├── GoogleDrive/
│   ├── WeTransfer/
│   └── Unknown/
├── _Documents/
│   ├── documents/
│   ├── csv/
│   └── code/
└── _Janitor/
    ├── reports/
    └── review/
        └── junk-candidates/
```

## Source normalization

macOS usually stores browser download provenance in:

```text
com.apple.metadata:kMDItemWhereFroms
```

The organizer reads this metadata with native `xattr` and maps common domains to stable categories.

Examples:

```text
facebook.com / fbcdn.net                  -> Facebook
messenger.com                             -> Messenger
instagram.com / cdninstagram.com          -> Instagram
tiktok.com / tiktok CDN hosts             -> TikTok
drive.google.com / drive.usercontent...   -> GoogleDrive
mail-attachment.googleusercontent.com     -> Gmail
wetransfer.com / download.wetransfer.com  -> WeTransfer
```

Common YouTube downloader/CDN domains are not preserved as random directory names when they can be safely recognized. If the host is a downloader and the filename also has recognizable YouTube characteristics, it becomes `YouTube`; otherwise it becomes the neutral `Downloader` group rather than pretending the downloader itself is the content source.

If no usable provenance exists, media goes under `Unknown` rather than being guessed.

## ChatGPT-origin files

```bash
./janitor gpt scan
./janitor gpt organize --apply
```

Two confidence levels are used:

- `confirmed`: ChatGPT/OpenAI/oaiusercontent provenance metadata; safe to move under `fromGPT`.
- `probable`: ChatGPT-like filename but missing origin metadata; review only by default.

To deliberately stage probable files:

```bash
./janitor gpt organize --apply --include-review
```

## Sensitive candidates

```bash
./janitor sensitive
```

This only counts sensitive-looking direct files in Downloads and never moves them. Use `--verbose` if you explicitly want their filenames shown locally.

Current conservative filename guards include terms associated with 2FA/TOTP, recovery or backup codes, passwords, credentials, private keys, tokens and identity documents.

This is a filename safety net, **not** a secrets scanner and not a guarantee that unflagged files are non-sensitive.

## Junk candidates

```bash
./janitor junk
```

Currently identifies direct top-level:

- zero-byte files;
- Office temporary/lock files beginning with `~$`.

They remain untouched by default. To move them into a review area, not delete them:

```bash
./janitor junk --apply
```

## PDF classification

```bash
./janitor pdf audit
```

The classifier uses macOS Spotlight text (`mdls kMDItemTextContent`), filename and download origin metadata. There is no OCR pass.

Built-in document categories include:

- bank statements;
- invoices;
- insurance documents;
- tax/NAV material;
- contracts.

Known vendor/source rules currently include Raiffeisen, Signal, NAV, OTP, Erste, K&H, CIB, UniCredit, MBH, MVM, E.ON, One/Vodafone, Telekom, Allianz, Generali and Groupama.

Only **high-confidence** classification moves directly into a semantic PDF category. Medium/low-confidence documents are routed to a proposed `_PDF/_Review/<vendor>/` location and remain in Downloads unless `--include-review` is explicitly supplied with `--apply`.

## ZIP lifecycle

### Extraction detection

```bash
./janitor zip audit
```

Creates:

- `_Janitor/reports/zip_extracted_report.csv`
- `_Janitor/reports/zip_extracted_report.txt`

This stage compares archive member paths and sizes. `EXACT_EXTRACTED` is still only a candidate state.

### CRC verification

```bash
./janitor zip verify
```

Creates:

- `_Janitor/reports/zip_crc_verified.csv`
- `_Janitor/reports/zip_crc_verified.txt`

Only complete matching archives become `CRC_VERIFIED`.

### Duplicate ZIP detection

```bash
./janitor zip duplicates
```

This reports two kinds of duplicate:

- `EXACT_BYTES`: identical SHA-256 archive files;
- `ZIP_LOGICAL_CONTENT`: archives whose normalized member names, sizes and CRCs are identical even if the ZIP container bytes differ because of recompression or metadata.

This is useful for `(1)`, `(2)` and repeatedly downloaded archive copies.

### Full ZIP report

```bash
./janitor zip report
```

Runs extraction detection, CRC verification and ZIP duplicate audit in sequence.

### Pair archive + extracted copy

```bash
./janitor zip pair
```

Dry-run only. Applying:

```bash
./janitor zip pair --apply
```

creates review bundles containing verified original ZIPs and their extracted counterpart. Several verified ZIP files mapping to the same extracted object are grouped into one bundle.

The pairer also understands archives previously staged below:

```text
_Janitor/review/extracted-zips/
```

## DMG handling

Top-level DMGs can be collected with:

```bash
./janitor dmg organize
./janitor dmg organize --apply
```

Exact duplicate DMGs are reported using SHA-256:

```bash
./janitor dmg audit
```

The DMG audit intentionally does **not** guess whether an application from a DMG is already installed.

## Reports

Reports are written locally under:

```text
~/Downloads/_Janitor/reports/
```

These reports can contain sensitive filenames and download URLs. They are intentionally excluded from Git by this repository's `.gitignore` and should not be uploaded to a public repository.

Use redacted organizer reports when a report must be shared:

```bash
./janitor scan --redact-report
```

## Safety rules

- No automatic deletion.
- Generic organizer scans only direct files in the Downloads root.
- Nested directories are not traversed or rearranged by the generic sorter.
- Sensitive-looking candidates are blocked from automatic moves.
- Filenames are hidden from terminal output unless `--verbose` is used.
- GPT provenance wins over extension-based sorting.
- Probable GPT filename matches remain review-only by default.
- Low/medium-confidence PDFs remain review-only by default.
- CRC failure means no ZIP pairing.
- ZIPs embedded in another extracted package are not automatically pulled out by the pairer.
- Broad/generic extracted directories such as `Main Files`, `Documentation`, `plugins`, `images`, `assets`, `resources`, etc. are skipped by the pairer.
- Overlapping extracted directory trees are skipped.
- Destination name collisions create a `__N` suffix rather than overwriting files.

## Install locally

```bash
git clone https://github.com/sipeti/macos-download-janitor.git
cd macos-download-janitor
chmod +x janitor scripts/*.py scripts/legacy/*.py
```

Then:

```bash
./janitor scan
```

The current tools use only Python's standard library plus native macOS `xattr` and `mdls` commands.

## Important ZIP note

CRC32 verifies ZIP member contents, not every possible archive-level metadata property. ZIPs can also contain permissions, symlink metadata, timestamps, comments, encryption metadata and directory entries. For this reason verified ZIPs and extracted copies are staged for human review rather than deleted automatically.

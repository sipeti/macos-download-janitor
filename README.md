# macOS Download Janitor

A conservative macOS `~/Downloads` cleanup toolkit.

Core rule:

> detect -> classify -> verify -> review -> move -> delete manually later

The project is intentionally report-first. Commands that can move files default to dry-run and require `--apply`.

## Unified CLI

The repository now has a single entry point:

```bash
./janitor --help
```

Recommended workflow:

```bash
./janitor scan
./janitor gpt scan
./janitor zip report
./janitor duplicates
./janitor pdf audit
./janitor media audit
./janitor dmg audit
```

Nothing above moves source files.

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

## Logical sorting order

The sorter uses this priority:

1. **Origin first**: anything carrying ChatGPT/OpenAI download metadata goes under `~/Downloads/fromGPT/`, regardless of extension.
2. **PDF meaning**: top-level PDFs are classified using filename, origin URL and Spotlight-indexed PDF text.
3. **Archive/install media**: ZIP, DMG, PKG/MPKG are separated into archive/install areas.
4. **Downloaded media by source**: MP4/MOV/images/audio are grouped by origin such as YouTube, Facebook, Instagram, TikTok, Vimeo or the actual source hostname.
5. **Generic documents/code**: top-level office files, CSVs and code/text files get a basic category folder.
6. **Nested content is never rearranged by the generic sorter**. It is listed as `WARN_NESTED` instead.

Existing managed directories such as `_Janitor`, `fromGPT`, `_ZIP`, `_PDF`, `_DMG`, `_Media` and `_Documents` are ignored by normal sorting passes.

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
│   └── other/
├── _PDF/
│   ├── Statements/
│   │   ├── Raiffeisen/
│   │   └── Unknown/
│   ├── Invoices/
│   ├── Insurance/
│   ├── Tax/
│   ├── Contracts/
│   ├── BySource/
│   └── Other/
├── _ZIP/
├── _DMG/
├── _Installers/
├── _Media/
│   ├── YouTube/video/
│   ├── Facebook/video/
│   ├── Instagram/images/
│   └── Unknown/
├── _Documents/
│   ├── documents/
│   ├── csv/
│   └── code/
└── _Janitor/
    ├── reports/
    └── review/
```

## PDF classification

The current classifier uses macOS Spotlight text (`mdls kMDItemTextContent`), filename and download origin metadata. There is no OCR pass.

Built-in document categories include:

- bank statements;
- invoices;
- insurance documents;
- tax/NAV material;
- contracts;
- source/vendor-based fallback.

Known vendor/source rules currently include Raiffeisen, Signal, NAV, OTP, Erste, K&H, CIB, UniCredit, MBH, MVM, E.ON, One/Vodafone, Telekom, Allianz, Generali and Groupama.

Classification carries a simple confidence level into the CSV report. Low-confidence PDFs can be reviewed before applying moves.

## Download origin grouping

macOS usually stores browser download provenance in:

```text
com.apple.metadata:kMDItemWhereFroms
```

The organizer reads this metadata directly with `xattr`. Media can therefore become, for example:

```text
_Media/YouTube/video/foo.mp4
_Media/Facebook/video/bar.mp4
_Media/Instagram/images/picture.jpg
_Media/example.com/images/logo.png
```

If the source is unavailable, the file goes under `Unknown` rather than being guessed.

## ChatGPT-origin files

```bash
./janitor gpt scan
./janitor gpt organize --apply
```

ChatGPT/OpenAI/oaiusercontent origins take precedence over generic PDF/media sorting, so GPT files remain together under `fromGPT`.

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

creates bundles such as:

```text
_Janitor/review/paired/Firmware_IB9369__9e70e7cd/
├── PAIR_INFO.txt
├── originals/
│   └── _ZIP/
│       └── Firmware_IB9369.zip
└── extracted/
    └── Firmware_IB9369/
```

Several verified ZIP files mapping to the same extracted object are grouped into one bundle.

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

The current DMG audit intentionally does **not** try to infer whether an application from a DMG is already installed. That requires a separate installer/application matching layer and should not be guessed from filenames alone.

## Generic organizer

Full dry-run:

```bash
./janitor scan
```

Apply all supported top-level moves:

```bash
./janitor organize --apply
```

Limit the pass if desired:

```bash
./janitor scan --only pdf
./janitor scan --only media
./janitor scan --only zip
./janitor scan --only dmg
```

For files already inside ordinary subdirectories, the sorter prints `WARN_NESTED`; it does not dismantle an existing directory hierarchy.

## Reports

Reports are written under:

```text
~/Downloads/_Janitor/reports/
```

Important reports include:

```text
downloads_sort_plan_all.csv
downloads_sort_plan_pdf.csv
downloads_sort_plan_media.csv
archive_duplicates_zip.csv
archive_duplicates_zip.txt
archive_duplicates_dmg.csv
archive_duplicates_dmg.txt
zip_extracted_report.csv
zip_crc_verified.csv
zip_pair_plan.csv
```

## Safety rules

- No automatic deletion.
- Generic organizer only moves top-level Downloads files.
- Nested files are warning/report-only.
- GPT provenance wins over extension-based sorting.
- CRC failure means no ZIP pairing.
- ZIPs embedded in another extracted package are not automatically pulled out.
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

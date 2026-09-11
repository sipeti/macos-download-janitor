# macOS Download Janitor

A small, dependency-free collection of macOS cleanup helpers built around a conservative rule:

> detect -> verify -> review -> move -> delete manually later

The scripts default to **read-only / dry-run** wherever a move is possible. They are intended for `~/Downloads` on macOS and use only the Python standard library plus native macOS metadata tools.

## Pipeline

### 1. Find ChatGPT-originated downloads

```bash
python3 scripts/gpt_download_inventory.py
```

Creates:

- `~/Downloads/fromGPT_inventory/gpt_inventory.csv`
- `~/Downloads/fromGPT_inventory/gpt_zip_contents.txt`

It checks `com.apple.metadata:kMDItemWhereFroms` and recognizes downloads from ChatGPT / OpenAI origins.

To organize the inventory:

```bash
python3 scripts/gpt_download_cleanup.py
```

Dry-run is the default. To apply:

```bash
python3 scripts/gpt_download_cleanup.py --apply
```

Files are moved under `~/Downloads/fromGPT/`; exact SHA-256 duplicates go to `_duplicates_review` instead of being deleted.

### 2. Detect ZIP archives that appear to have been extracted

```bash
python3 scripts/zip_extract_audit.py
```

Creates:

- `~/Downloads/_Janitor/reports/zip_extracted_report.csv`
- `~/Downloads/_Janitor/reports/zip_extracted_report.txt`

This stage compares paths and sizes only. `EXACT_EXTRACTED` is a candidate status, **not yet permission to delete an archive**.

### 3. Verify exact candidates by CRC32

```bash
python3 scripts/zip_crc_verify.py
```

Creates:

- `~/Downloads/_Janitor/reports/zip_crc_verified.csv`
- `~/Downloads/_Janitor/reports/zip_crc_verified.txt`

Only archives that match every relevant ZIP member are marked `CRC_VERIFIED`.

### 4. Pair the original ZIP with its extracted copy

```bash
python3 scripts/janitor_pair_zips.py
```

This is a dry-run. It prints the proposed bundles and writes:

- `~/Downloads/_Janitor/reports/zip_pair_plan.csv`

To apply the moves:

```bash
python3 scripts/janitor_pair_zips.py --apply
```

The result looks like:

```text
~/Downloads/_Janitor/review/paired/
  Firmware_IB9369__9e70e7cd/
    PAIR_INFO.txt
    originals/
      _ZIP/
        Firmware_IB9369.zip
    extracted/
      Firmware_IB9369/
```

If several verified ZIP files map to the same extracted object, they are grouped into one bundle.

The pairer also understands the staging layout produced by the older review mover:

```text
~/Downloads/_Janitor/review/extracted-zips/
```

so previously staged archives do not need to be restored first.

## Safety rules

The current scripts deliberately avoid several risky actions:

- no automatic deletion;
- CRC failure means no pairing;
- ZIP files embedded inside another extracted package are not pulled out automatically;
- broad/generic extracted directories such as `Main Files`, `Documentation`, `plugins`, `images`, `assets`, `resources`, etc. are skipped;
- overlapping extracted directory trees are skipped;
- moves require `--apply` and an explicit `yes` confirmation where applicable.

## Legacy helper

`scripts/legacy/janitor_zip_review.py` is the earlier staging helper. It moves CRC-verified top-level / `_ZIP` archives to `_Janitor/review/extracted-zips/`. New runs should generally use `janitor_pair_zips.py` instead, but the file is kept because existing staged data may have been produced by it.

## Install locally

Clone the repository and optionally make the scripts executable:

```bash
git clone https://github.com/sipeti/macos-download-janitor.git
cd macos-download-janitor
chmod +x scripts/*.py scripts/legacy/*.py
```

All tools default to `~/Downloads`; no third-party Python packages are required.

## Important note

CRC32 proves that the verified file contents match the ZIP members. ZIP archives can also carry metadata such as permissions, symlink information, timestamps, comments, encryption metadata, and directory entries. For that reason the project intentionally stages verified ZIPs and extracted copies for human review instead of deleting them automatically.

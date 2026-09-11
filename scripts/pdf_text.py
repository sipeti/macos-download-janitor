#!/usr/bin/env python3

"""Local-only PDF text extraction helpers for macOS Download Janitor.

Extraction order:
1. Spotlight metadata (fast, no parsing)
2. pdftotext when installed
3. native macOS PDFKit helper compiled from helpers/pdf_text_extract.swift

No OCR and no network access are used here.
"""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PDFKIT_SOURCE = REPO_ROOT / "helpers" / "pdf_text_extract.swift"
PDFKIT_CACHE_DIR = Path.home() / "Library" / "Caches" / "macos-download-janitor"
PDFKIT_BINARY = PDFKIT_CACHE_DIR / "pdf_text_extract"


def spotlight_text(path, limit=250000):
    try:
        result = subprocess.run(
            ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = result.stdout.strip()
        if text and text != "(null)":
            return text[:limit]
    except Exception:
        pass
    return ""


def ensure_pdfkit_helper():
    """Return (binary_path_or_None, status_string)."""
    if not PDFKIT_SOURCE.is_file():
        return None, "helper-source-missing"

    try:
        if PDFKIT_BINARY.is_file() and PDFKIT_BINARY.stat().st_mtime >= PDFKIT_SOURCE.stat().st_mtime:
            return PDFKIT_BINARY, "cached"
    except OSError:
        pass

    swiftc = shutil.which("swiftc")
    if not swiftc:
        xcrun = shutil.which("xcrun")
        if xcrun:
            try:
                result = subprocess.run(
                    [xcrun, "--find", "swiftc"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    swiftc = result.stdout.strip()
            except Exception:
                pass

    if not swiftc:
        return None, "swiftc-unavailable"

    try:
        PDFKIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [swiftc, str(PDFKIT_SOURCE), "-framework", "PDFKit", "-o", str(PDFKIT_BINARY)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and PDFKIT_BINARY.is_file():
            return PDFKIT_BINARY, "compiled"
        msg = (result.stderr or result.stdout).strip().splitlines()
        return None, (msg[-1][:200] if msg else "compile-failed")
    except Exception as exc:
        return None, f"compile-error:{str(exc)[:160]}"


def _pdftotext(path, max_pages, limit):
    binary = shutil.which("pdftotext")
    if not binary:
        return ""
    try:
        result = subprocess.run(
            [binary, "-f", "1", "-l", str(max_pages), "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode == 0:
            return result.stdout[:limit]
    except Exception:
        pass
    return ""


def _pdfkit(path, helper, max_pages, limit):
    if not helper:
        return ""
    try:
        result = subprocess.run(
            [str(helper), str(path), str(max_pages)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode == 0:
            return result.stdout[:limit]
    except Exception:
        pass
    return ""


def extract_pdf_text(path, max_pages=3, allow_pdftotext=True, allow_pdfkit=True, limit=250000):
    """Return (text, backend, diagnostics).

    diagnostics contains backend availability/status only and never document text.
    """
    path = Path(path)
    diagnostics = {
        "pdftotext": "available" if shutil.which("pdftotext") else "unavailable",
        "pdfkit": "disabled",
    }

    text = spotlight_text(path, limit=limit)
    if len(text.strip()) >= 80:
        return text, "spotlight", diagnostics

    best = text
    backend = "spotlight" if text.strip() else "none"

    if allow_pdftotext:
        candidate = _pdftotext(path, max_pages=max_pages, limit=limit)
        if len(candidate.strip()) > len(best.strip()):
            best, backend = candidate, "pdftotext"

    helper = None
    if allow_pdfkit:
        helper, status = ensure_pdfkit_helper()
        diagnostics["pdfkit"] = status
        candidate = _pdfkit(path, helper, max_pages=max_pages, limit=limit)
        if len(candidate.strip()) > len(best.strip()):
            best, backend = candidate, "pdfkit"

    return best, backend, diagnostics

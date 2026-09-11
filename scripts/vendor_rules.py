#!/usr/bin/env python3

"""Built-in and private local PDF vendor rules.

Public code contains only generic built-in rules. Rules learned from a user's PDFs are
stored locally under ~/Downloads/_Janitor/config/vendor_rules.json and are never
written back to the repository.
"""

import json
import re
from pathlib import Path

BUILTIN_VENDOR_RULES = [
    ("Raiffeisen", ("raiffeisen", "raiffeisen.hu")),
    ("Signal", ("signal iduna", "signal-iduna", "signal.hu", "signal biztosító", "signal biztosito")),
    ("NAV", ("nav.gov.hu", "nemzeti adó- és vámhivatal", "nemzeti ado- es vamhivatal")),
    ("OTP", ("otpbank", "otp bank", "otpbank.hu")),
    ("Erste", ("erste bank", "erstebank", "erstebank.hu")),
    ("K&H", ("k&h bank", "kh.hu")),
    ("CIB", ("cib bank", "cib.hu")),
    ("UniCredit", ("unicredit", "unicreditbank.hu")),
    ("MBH", ("mbh bank", "mbhbank", "mkb bank", "takarékbank", "takarekbank")),
    ("MVM", ("mvm", "mvmnext", "mvmnext.hu")),
    ("EON", ("eon.hu", "e.on", "eon energia")),
    ("One", ("one.hu", "vodafone", "vodafone.hu")),
    ("Telekom", ("telekom.hu", "magyar telekom")),
    ("Allianz", ("allianz", "allianz.hu")),
    ("Generali", ("generali", "generali.hu")),
    ("Groupama", ("groupama", "groupama.hu")),
]

LEGAL_SUFFIX_RE = re.compile(
    r"(?:^|\s)(?:Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Ltd\.?|Limited|GmbH|AG|Inc\.?|LLC|B\.V\.|S\.A\.|SAS|s\.r\.o\.|Sp\.?\s+z\.?\s+o\.?o\.?)\s*$",
    re.I,
)
LEGAL_SUFFIX_ANY_RE = re.compile(
    r"\b(?:Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Ltd\.?|Limited|GmbH|AG|Inc\.?|LLC|B\.V\.|S\.A\.|SAS|s\.r\.o\.|Sp\.?\s+z\.?\s+o\.?o\.?)\b",
    re.I,
)

# Typical prose fragments that can end in a legal-looking word such as "LIMITED".
# A real company may contain one of these words; several together strongly indicate
# that PDF text extraction captured a sentence instead of an entity name.
NARRATIVE_TOKENS = {
    "either", "express", "implied", "including", "but", "not", "without",
    "warranty", "warranties", "liability", "liable", "including", "merchantability",
}


def local_rules_path(root):
    return Path(root) / "_Janitor" / "config" / "vendor_rules.json"


def _clean_rule(name, markers):
    name = str(name).strip()
    clean_markers = []
    seen = set()
    for marker in markers or []:
        marker = str(marker).strip()
        key = marker.lower()
        if marker and key not in seen:
            clean_markers.append(marker)
            seen.add(key)
    if not name or not clean_markers:
        return None
    return name, tuple(clean_markers)


def validate_learned_vendor_name(name):
    """Return (is_valid, reason) for a vendor name learned from private PDFs.

    Learned rules are stricter than built-ins because PDF text extraction may turn
    prose, copyright lines or several adjacent companies into one fake entity.
    """
    name = str(name).strip()
    if not name:
        return False, "empty-name"
    if "\n" in name or "\r" in name:
        return False, "multiline-name"
    if len(name) > 100:
        return False, "name-too-long"
    if name.startswith(("©", "(", "[", "{")) or "©" in name:
        return False, "suspicious-prefix"

    suffixes = LEGAL_SUFFIX_ANY_RE.findall(name)
    if len(suffixes) != 1:
        return False, "expected-exactly-one-legal-suffix"
    if not LEGAL_SUFFIX_RE.search(name):
        return False, "legal-suffix-not-at-end"

    words = re.findall(r"\S+", name)
    if len(words) < 2:
        return False, "too-short"
    if len(words) > 12:
        return False, "too-many-words"

    normalized_words = {
        re.sub(r"[^a-z]+", "", word.lower())
        for word in words
    }
    normalized_words.discard("")
    if len(normalized_words & NARRATIVE_TOKENS) >= 3:
        return False, "narrative-text-not-company"

    return True, "ok"


def _raw_local_rules(root):
    path = local_rules_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("rules", [])
    return data if isinstance(data, list) else []


def load_local_vendor_rules_with_rejections(root):
    rules = []
    rejected = []
    for item in _raw_local_rules(root):
        if isinstance(item, dict):
            cleaned = _clean_rule(item.get("name", ""), item.get("markers", []))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            cleaned = _clean_rule(item[0], item[1])
        else:
            cleaned = None

        if not cleaned:
            rejected.append(("[invalid rule]", "malformed-rule"))
            continue

        name, markers = cleaned
        valid, reason = validate_learned_vendor_name(name)
        if valid:
            rules.append((name, markers))
        else:
            rejected.append((name, reason))
    return rules, rejected


def load_local_vendor_rules(root):
    rules, _rejected = load_local_vendor_rules_with_rejections(root)
    return rules


def all_vendor_rules(root=None):
    local = load_local_vendor_rules(root) if root is not None else []
    # Local rules come first so a reviewed local alias can override a broad built-in rule.
    return [*local, *BUILTIN_VENDOR_RULES]


def match_vendor(haystack, rules=None):
    h = (haystack or "").lower()
    rules = rules if rules is not None else BUILTIN_VENDOR_RULES
    scores = []
    for vendor, markers in rules:
        hits = [marker for marker in markers if marker.lower() in h]
        if hits:
            score = (len(hits), max(len(marker) for marker in hits), sum(len(marker) for marker in hits))
            scores.append((score, vendor))
    return max(scores)[1] if scores else "Unknown"


def save_local_vendor_rules(root, rules, metadata=None):
    path = local_rules_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Rewriting the file through this function also drops previously learned malformed
    # rules that the stricter loader rejected.
    existing = {name.lower(): (name, list(markers)) for name, markers in load_local_vendor_rules(root)}
    rejected_new = []

    for name, markers in rules:
        valid, reason = validate_learned_vendor_name(name)
        if not valid:
            rejected_new.append((name, reason))
            continue
        key = name.lower().strip()
        old_name, old_markers = existing.get(key, (name, []))
        seen = {m.lower() for m in old_markers}
        for marker in markers:
            marker = str(marker).strip()
            if marker and marker.lower() not in seen:
                old_markers.append(marker)
                seen.add(marker.lower())
        existing[key] = (old_name, old_markers)

    payload = {
        "version": 2,
        "private_local_file": True,
        "note": "Generated from local PDF evidence. Do not commit or share without review.",
        "rules": [
            {"name": name, "markers": markers}
            for name, markers in sorted(existing.values(), key=lambda x: x[0].lower())
        ],
    }
    if metadata:
        payload["metadata"] = metadata
    if rejected_new:
        payload["last_rejected_candidates"] = [
            {"name": name, "reason": reason} for name, reason in rejected_new
        ]

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

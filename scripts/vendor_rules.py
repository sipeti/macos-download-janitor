#!/usr/bin/env python3

"""Built-in and private local PDF vendor rules.

Public code contains only generic built-in rules. Rules learned from a user's PDFs are
stored locally under ~/Downloads/_Janitor/config/vendor_rules.json and are never
written back to the repository.
"""

import json
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


def local_rules_path(root):
    return Path(root) / "_Janitor" / "config" / "vendor_rules.json"


def _clean_rule(name, markers):
    name = str(name).strip()
    clean_markers = []
    for marker in markers or []:
        marker = str(marker).strip()
        if marker and marker.lower() not in {m.lower() for m in clean_markers}:
            clean_markers.append(marker)
    if not name or not clean_markers:
        return None
    return name, tuple(clean_markers)


def load_local_vendor_rules(root):
    path = local_rules_path(root)
    if not path.is_file():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list):
        return []

    rules = []
    for item in data:
        if isinstance(item, dict):
            cleaned = _clean_rule(item.get("name", ""), item.get("markers", []))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            cleaned = _clean_rule(item[0], item[1])
        else:
            cleaned = None
        if cleaned:
            rules.append(cleaned)
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
            # Prefer multiple and longer markers to avoid broad aliases winning ties.
            score = (len(hits), max(len(marker) for marker in hits), sum(len(marker) for marker in hits))
            scores.append((score, vendor))
    return max(scores)[1] if scores else "Unknown"


def save_local_vendor_rules(root, rules, metadata=None):
    path = local_rules_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = {name.lower(): (name, list(markers)) for name, markers in load_local_vendor_rules(root)}
    for name, markers in rules:
        key = name.lower().strip()
        if not key:
            continue
        old_name, old_markers = existing.get(key, (name, []))
        seen = {m.lower() for m in old_markers}
        for marker in markers:
            if marker and marker.lower() not in seen:
                old_markers.append(marker)
                seen.add(marker.lower())
        existing[key] = (old_name, old_markers)

    payload = {
        "version": 1,
        "private_local_file": True,
        "note": "Generated from local PDF evidence. Do not commit or share without review.",
        "rules": [
            {"name": name, "markers": markers}
            for name, markers in sorted(existing.values(), key=lambda x: x[0].lower())
        ],
    }
    if metadata:
        payload["metadata"] = metadata

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

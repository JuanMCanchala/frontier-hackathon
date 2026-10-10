"""Scrub credentials from text before it is stored or returned."""

import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"

# Only well-known credential formats: generic "long alphanumeric" rules would erase
# legitimate answers such as invoice or order codes.
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}"),
]
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;\"']+")


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace the exact secrets given and anything shaped like a known credential."""
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _BEARER.sub(f"Bearer {REDACTED}", text)

"""Remove identifying details before text leaves the server.

The patterns are intentionally conservative: a false positive only makes an AI
answer less specific, while a false negative would send personal data. Named
values are supplied by the calling feature when it already knows them (for
example the student's name on a report card), so they can be replaced exactly.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+263|0)?(?:17|71|77|78|73)\d{7}(?!\d)|\+\d[\d\s-]{7,}\d"
)
ID_RE = re.compile(
    r"\b\d{2}[- ]?\d{6}[A-Z]\d{2}\b"
    r"|\b\d{7,9}[A-Za-z]\d{0,2}\b"
    r"|\b[A-Z]{2}\d{6,8}\b"
)
ADDRESS_RE = re.compile(
    r"(?im)^[^\n]*\b(?:street|st\.|road|rd\.|avenue|ave\.|drive|close|lane|way|"
    r"suite|flat|plot|p\.?o\.? box)\b[^\n]*$"
)
LABEL_RE = re.compile(
    r"(?im)\b(name|full name|student|teacher|guardian|patient|contact|parent|"
    r"email|phone|id)\s*[:=]\s*[^\n,;]+"
)
PLACEHOLDER_RE = re.compile(
    r"\[(?:name|email|phone|id|address|link|redacted)\]", re.I
)


def redact_text(text, names=()):
    """Replace names, ID numbers, phones, emails and addresses in ``text``."""
    if text is None:
        return ""
    result = str(text)
    # Structured values first: an email containing a person's name must stay
    # an [email], and a known name must not leave a fragment behind.
    result = EMAIL_RE.sub("[email]", result)
    result = URL_RE.sub("[link]", result)
    result = PHONE_RE.sub("[phone]", result)
    result = ID_RE.sub("[id]", result)
    result = ADDRESS_RE.sub("[address]", result)
    values = sorted(
        {str(name).strip() for name in names if str(name).strip()},
        key=len,
        reverse=True,
    )
    for value in values:
        if len(value) >= 2:
            result = re.sub(re.escape(value), "[name]", result, flags=re.I)
    def _label(match):
        # A structured placeholder is already safer than a generic label.
        if PLACEHOLDER_RE.search(match.group(0)):
            return match.group(0)
        return f"{match.group(1)}: [redacted]"

    return LABEL_RE.sub(_label, result)


def safe_summary(text, names=(), limit=160):
    """A short redacted prompt description for the usage log."""
    clean = " ".join(redact_text(text, names).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"

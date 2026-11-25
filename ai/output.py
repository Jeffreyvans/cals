"""Clean up model output before it is shown to a person.

Model text is treated as untrusted data. This module removes links, command-like
lines and control characters and caps the length. Later safety work adds
injection, profanity and harm checks on top of this shared boundary.
"""

from __future__ import annotations

import re

URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{0,120})\]\([^)]{0,500}\)")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]{0,200}>")
CODE_FENCE_RE = re.compile(r"^\s*```.*$", re.M)
COMMAND_LINE_RE = re.compile(
    r"^[ \t]*(?:[>$!#/]|rm\s|sudo\s|curl\s|wget\s|delete\s|drop\s|update\s|"
    r"insert\s|select\s|execute\s|run\s|ignore\s|forget\s)[^\n]*",
    re.I | re.M,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_links(text: str) -> str:
    """Remove URLs and turn Markdown links into their harmless label."""
    result = MARKDOWN_LINK_RE.sub(lambda match: match.group(1), text or "")
    return URL_RE.sub("[link removed]", result)


def strip_commands(text: str) -> str:
    """Drop lines that try to issue a command rather than answer the request."""
    result = CODE_FENCE_RE.sub("", text or "")
    return COMMAND_LINE_RE.sub("", result)


def sanitize_output(text, limit: int = 1200) -> str:
    """Return short, plain text safe to display as a draft or answer."""
    result = CONTROL_RE.sub("", str(text or ""))
    result = strip_links(result)
    result = HTML_TAG_RE.sub("", result)
    result = strip_commands(result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = "\n".join(line.rstrip() for line in result.splitlines()).strip()
    cap = max(1, limit)
    if len(result) > cap:
        result = result[: cap - 1].rstrip() + "…"
    return result

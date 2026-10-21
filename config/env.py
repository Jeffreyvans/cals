"""Tiny helpers for reading configuration from environment variables."""
import os
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_str(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    raise ValueError(f"{name} must be a true/false value, got {raw!r}.")


def env_list(name, default=None):
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name, default=0):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}.")


def load_dotenv(path):
    """Read KEY=VALUE lines from a .env file into os.environ.

    Variables already set in the environment win. Values may be quoted;
    an unquoted value may carry a trailing ``# comment``.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ[key] = value

"""Institution colours and the text that stays readable on them.

An institution can pick its own primary and accent colour. A colour that
cannot carry readable text is refused and the default is used instead, so a
school never ends up with white text on a white sidebar.
"""

DEFAULT_PRIMARY = "#12403c"
DEFAULT_ACCENT = "#e8a317"
INK = "#1d2b2a"
WHITE = "#ffffff"

# The sidebar and the buttons use these two, so they are the pairs that have
# to reach the 4.5:1 contrast ratio of WCAG 2.1 AA.
MINIMUM_RATIO = 4.5


def parse_hex(value):
    """Return ``(r, g, b)`` for ``#rgb`` or ``#rrggbb``, or None."""
    if not value:
        return None
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _channel(value):
    """The linear-light value of one colour channel."""
    share = value / 255
    if share <= 0.03928:
        return share / 12.92
    return ((share + 0.055) / 1.055) ** 2.4


def luminance(colour):
    """The relative luminance of a colour, from 0 (black) to 1 (white)."""
    rgb = parse_hex(colour)
    if rgb is None:
        return None
    red, green, blue = (_channel(part) for part in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """The WCAG contrast ratio between two colours, from 1 to 21."""
    one = luminance(first)
    other = luminance(second)
    if one is None or other is None:
        return None
    lighter, darker = max(one, other), min(one, other)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text(background, candidates=(WHITE, INK)):
    """The candidate text colour with the best contrast on the background."""
    best = None
    best_ratio = -1
    for candidate in candidates:
        ratio = contrast_ratio(candidate, background)
        if ratio is None:
            continue
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best


def usable_colour(value, fallback):
    """Return the colour when it is a real colour, otherwise the fallback."""
    if parse_hex(value) is None:
        return fallback
    return str(value).strip()


def primary_colour(institution):
    """The sidebar colour of an institution."""
    if institution is None:
        return DEFAULT_PRIMARY
    return usable_colour(institution.primary_color, DEFAULT_PRIMARY)


def accent_colour(institution):
    """The accent colour of an institution."""
    if institution is None:
        return DEFAULT_ACCENT
    return usable_colour(institution.accent_color, DEFAULT_ACCENT)


def brand_variables(institution):
    """The CSS variables that carry the institution's branding."""
    primary = primary_colour(institution)
    accent = accent_colour(institution)
    return {
        "--side": primary,
        "--side-text": readable_text(primary, (WHITE, INK)),
        "--accent": accent,
        "--accent-text": readable_text(accent, (INK, WHITE)),
    }

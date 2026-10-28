"""Normalisation de texte et lecture des nombres au format français."""

import html
import re
import unicodedata

_SPACES = "    "
_SPACE_CLASS = "[     ]"

_NUMBER_RE = re.compile(
    r"(?<![\d.,])("
    rf"\d{{1,3}}(?:{_SPACE_CLASS}\d{{3}})+(?:[.,]\d+)?"
    r"|\d{1,3}(?:\.\d{3})+,\d+"
    r"|\d+(?:[.,]\d+)?"
    r")(?![\d])"
)


def normalize(text):
    """Minuscules, sans accents, espaces et apostrophes unifiés."""
    if not text:
        return ""
    text = html.unescape(str(text))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace("‘", "'")
    for space in _SPACES:
        text = text.replace(space, " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_number(value):
    """'18 450,75 €' -> 18450.75 ; '6 %' -> 6.0 ; None si illisible."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    for space in _SPACES + " ":
        text = text.replace(space, "")
    text = re.sub(r"[€%]|eur(os?)?$", "", text, flags=re.IGNORECASE).strip()
    if not re.fullmatch(r"-?[\d.,]+", text or "x"):
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def find_numbers(text):
    """Tous les nombres d'un texte, groupés ('1 250') et isolés ('1', '250')."""
    found = []
    for match in _NUMBER_RE.finditer(text or ""):
        token = match.group(1)
        number = parse_number(token)
        if number is not None:
            found.append(number)
        if re.search(_SPACE_CLASS, token):
            for part in re.split(_SPACE_CLASS, token):
                number = parse_number(part)
                if number is not None:
                    found.append(number)
    return found


def number_positions(text, value, tolerance=0.005):
    """Positions (début, fin) des occurrences de `value` dans `text`."""
    positions = []
    for match in _NUMBER_RE.finditer(text or ""):
        number = parse_number(match.group(1))
        if number is not None and abs(number - value) <= tolerance:
            positions.append((match.start(1), match.end(1)))
    return positions


def contains_number(text, value, tolerance=0.005):
    return any(abs(n - value) <= tolerance for n in find_numbers(text))


def format_number(value, kind):
    if value is None:
        return ""
    if kind == "money":
        formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",")
        return f"{formatted} €"
    if float(value).is_integer():
        formatted = f"{int(value):,}".replace(",", " ")
    else:
        formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} %" if kind == "percent" else formatted

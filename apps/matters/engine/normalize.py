"""Validators/normalisers per data type.

Each normaliser receives the raw text that follows an anchor and returns
(matched_substring, offset_in_raw, normalized_value) or None when the text does
not look like a valid value of that type.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from apps.core.utils import money

UPPER = "A-ZÄÖÜ"
LETTER = "A-Za-zÄÖÜäöüßéèáàçñ"

GERMAN_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}

NAME_RE = re.compile(
    rf"(?:(?:Herr|Frau|Herrn)\s+)?((?:(?:Dr|Prof)\.\s*)*[{UPPER}][{LETTER}'\-]+(?:\s+(?:von|van|de|zu)?\s*[{UPPER}][{LETTER}'\-.]+){{1,3}}"
    rf"(?:\s+(?:GmbH|UG \(haftungsbeschränkt\)|AG|KG|e\.\s?K\.|mbH))?)"
)
MONEY_RE = re.compile(
    r"(?:€|EUR|Euro)?\s*(\d{1,3}(?:[.,\s’']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR|Euro)?",
    re.IGNORECASE,
)
DATE_NUM_RE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\b")
DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_TXT_RE = re.compile(r"\b(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(\d{4})\b")
GRUNDBUCH_RE = re.compile(
    rf"(?:Grundbuch\s+(?:von|für)\s+)?([{UPPER}][{LETTER}\-]+(?:\s[{UPPER}][{LETTER}\-]+)?)[\s,;:–-]{{1,6}}(?:Band\s+\d+[\s,]+)?Blatt\s*(?:Nr\.?\s*)?(\d{{1,6}})"
)
PARCEL_RE = re.compile(r"\b(?:Nr\.?\s*)?(\d{1,6}(?:/\d{1,4})?)\b")
ADDRESS_RE = re.compile(
    rf"([{UPPER}][{LETTER}.\-]+(?:\s[{LETTER}.\-]+){{0,3}}\s\d{{1,4}}\s?[a-zA-Z]?,?\s+\d{{5}}\s+[{UPPER}][{LETTER}\-]+(?:\s(?:am|an der|im|in der)\s[{UPPER}][{LETTER}\-]+)?)"
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,;.\t")


def parse_money(raw: str):
    m = MONEY_RE.search(raw)
    if not m:
        return None
    digits = m.group(1).replace(" ", "").replace("’", "").replace("'", "")
    last_sep = max(digits.rfind("."), digits.rfind(","))
    if last_sep != -1 and len(digits) - last_sep - 1 in (1, 2):
        whole = re.sub(r"[.,]", "", digits[:last_sep])
        number = f"{whole}.{digits[last_sep + 1:]}"
    else:
        number = re.sub(r"[.,]", "", digits)
    try:
        amount = money(number)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return m.group(0).strip(), m.start() + (len(m.group(0)) - len(m.group(0).lstrip())), str(amount)


def parse_date(raw: str):
    for rx, order in ((DATE_NUM_RE, "dmy"), (DATE_ISO_RE, "ymd"), (DATE_TXT_RE, "dMy")):
        m = rx.search(raw)
        if not m:
            continue
        try:
            if order == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif order == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                d, y = int(m.group(1)), int(m.group(3))
                mo = GERMAN_MONTHS.get(m.group(2).lower())
                if not mo:
                    continue
            value = date(y, mo, d)
        except ValueError:
            continue
        if not 1900 <= value.year <= 2100:
            continue
        return m.group(0), m.start(), value.isoformat()
    return None


def parse_name(raw: str):
    head = re.split(r"[,;(\n]|\s{2,}|\sgeb\.|\swohnhaft", raw, maxsplit=1)[0]
    m = NAME_RE.search(head)
    if not m:
        return None
    value = _clean(m.group(1))
    if len(value.split()) < 2:
        return None
    return m.group(1), m.start(1), value


def parse_address(raw: str):
    m = ADDRESS_RE.search(raw)
    if not m:
        return None
    return m.group(1), m.start(1), _clean(m.group(1)).replace(" ,", ",")


def parse_grundbuch(raw: str):
    m = GRUNDBUCH_RE.search(raw)
    if not m:
        return None
    return m.group(0), m.start(), f"{m.group(1)} Blatt {m.group(2)}"


def parse_parcel(raw: str):
    m = PARCEL_RE.search(raw)
    if not m:
        return None
    return m.group(1), m.start(1), m.group(1)


def parse_text(raw: str):
    line = raw.split("\n", 1)[0]
    value = _clean(line)
    if len(value) < 2:
        return None
    start = line.find(value[:3]) if value else 0
    return value, max(start, 0), value[:300]


PARSERS = {
    "name": parse_name,
    "address": parse_address,
    "money": parse_money,
    "date": parse_date,
    "grundbuch": parse_grundbuch,
    "parcel": parse_parcel,
    "text": parse_text,
}


def normalize(data_type: str, raw: str):
    return PARSERS.get(data_type, parse_text)(raw)


def normalize_value(data_type: str, raw: str) -> str:
    """Normalise a value a person typed. Falls back to the cleaned text."""
    parsed = normalize(data_type, raw)
    return parsed[2] if parsed else _clean(raw)


def same_value(a: str, b: str) -> bool:
    return _clean(a).casefold() == _clean(b).casefold()


def to_decimal(value: str):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None

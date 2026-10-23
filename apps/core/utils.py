from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.utils import timezone
from django.utils.crypto import get_random_string

REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_ref(prefix: str) -> str:
    """PREFIX-YY-XXXXXX using an alphabet without ambiguous characters."""
    yy = timezone.now().strftime("%y")
    return f"{prefix}-{yy}-{get_random_string(6, REF_ALPHABET)}"


def unique_ref(model, prefix: str, field: str = "ref") -> str:
    while True:
        ref = make_ref(prefix)
        if not model.objects.filter(**{field: ref}).exists():
            return ref


def money(value) -> Decimal:
    """Exact money: never float. Accepts str/int/Decimal; rounds half-up to cents."""
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Not a money amount: {value!r}")


def format_eur(value) -> str:
    amount = money(value)
    whole, cents = f"{amount:,.2f}".split(".")
    return f"€{whole}.{cents}"


def pct(value) -> int:
    """Confidence 0..1 → whole percent, half-up."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

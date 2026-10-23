import re

from django.core.exceptions import ValidationError

PATH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def validate_paths(paths):
    """Reject duplicates and fields that would overwrite a parent object."""
    paths = list(paths)
    for path in paths:
        if not isinstance(path, str) or len(path) > 120 or not PATH_RE.fullmatch(path):
            raise ValidationError("Destination paths must use names such as Beteiligter.Kaeufer.Name.")
    ordered = sorted(paths)
    for left, right in zip(ordered, ordered[1:]):
        if right == left or right.startswith(left + "."):
            raise ValidationError("Destination fields overlap. Give every value a unique leaf path.")


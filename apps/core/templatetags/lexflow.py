from django import template

register = template.Library()

MATTER_TONES = {
    "unclassified": "seal",
    "needs_review": "amber",
    "ready": "ink",
    "approved": "moss",
    "transferred": "muted",
}


@register.filter
def matter_tone(status):
    return MATTER_TONES.get(status, "muted")


@register.filter
def initials(user):
    name = (user.get_full_name() or user.get_username()).split()
    return "".join(p[0] for p in name[:2]).upper()


@register.filter
def meter_tone(field):
    if field.status in ("conflict", "missing"):
        return "seal"
    if field.status == "review":
        return "amber"
    if field.status in ("confirmed", "corrected"):
        return "ink"
    return "moss"


@register.filter
def pct_tone(pct):
    try:
        p = int(pct)
    except (TypeError, ValueError):
        return "muted"
    if p >= 90:
        return "moss"
    if p >= 70:
        return "amber"
    return "seal"


@register.filter
def stage_state(index, matter):
    """CSS state for stage `index` (1-based) of a matter's 7-stage progress."""
    now = matter.stage_index
    if matter.status == "transferred":
        return "done"
    if index < now:
        return "done"
    if index == now:
        blocked = matter.status == "unclassified" or matter.open_conflicts.exists()
        return "now blocked" if blocked else "now"
    return ""


@register.filter
def audit_tone(event):
    if event.action in ("field.conflict_detected", "policy.external_ai_blocked") or "failed" in event.action:
        return "warn"
    return "" if event.actor_id is None else "human"


@register.filter
def doc_icon(att):
    name = att.filename.lower()
    if att.kind == "body":
        return "bi-envelope"
    if name.endswith(".pdf"):
        return "bi-file-earmark-pdf"
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        return "bi-file-earmark-image"
    return "bi-file-earmark-text"


@register.filter
def better(metric):
    b, p = metric.get("baseline"), metric.get("pilot")
    if b is None or p is None:
        return ""
    if metric["better"] == "lower":
        return "good" if p < b else ""
    return "good" if p > b else ""

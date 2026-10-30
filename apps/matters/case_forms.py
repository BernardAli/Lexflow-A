"""Ordinary staff forms for the versioned case model."""
from datetime import date
from decimal import Decimal

from django import forms

from .case_data import METADATA_FIELDS, PARTICIPANT_FIELDS, PROPERTY_FIELDS, bindings, canonical_case

ROLES = [(v, v.replace("_", " ").title()) for v in (
    "buyer", "seller", "company", "director", "shareholder", "principal", "attorney", "representative", "authority", "other")]


class CaseDetailForm(forms.Form):
    def clean(self):
        cleaned = super().clean()
        if getattr(self, "new_property", False) and not any(cleaned.get(key) for key in ("address", "land_register", "parcel", "external_id")):
            raise forms.ValidationError("Enter an address, land register, parcel or destination ID for this property.")
        share = cleaned.get("ownership_share", "")
        if share:
            try:
                numerator, denominator = (int(x) for x in share.split("/"))
                if not 0 < numerator <= denominator:
                    raise ValueError
            except (ValueError, TypeError):
                self.add_error("ownership_share", "Use a fraction such as 1/2 with a positive denominator.")
        for key, value in list(cleaned.items()):
            if isinstance(value, date):
                cleaned[key] = value.isoformat()
            elif isinstance(value, Decimal):
                cleaned[key] = format(value, "f")
            elif value is None:
                cleaned[key] = ""
        return cleaned


def detail_form(matter, group, record_id="", *, data=None, prefix=None):
    case = canonical_case(matter)
    initial = case["metadata"] if group == "metadata" else next(
        (item for item in case[group] if item["id"] == record_id), {})
    primary = bindings(matter, group).get(record_id)
    locked = set(primary[1]) | ({"role"} if "role" in primary[0] else set()) if primary else set()
    labels = {"metadata": METADATA_FIELDS, "participants": PARTICIPANT_FIELDS, "properties": PROPERTY_FIELDS}[group]
    form = CaseDetailForm(data=data, initial=initial, prefix=prefix)
    form.new_property = group == "properties" and not primary
    for key, label in labels.items():
        required = group == "participants" and key in ("kind", "role", "name") and key not in locked
        field = forms.CharField(label=label, max_length=500, required=required)
        if key == "kind":
            field = forms.ChoiceField(label=label, choices=[("natural", "Natural person"), ("legal", "Legal entity")])
        elif key == "role":
            field = forms.ChoiceField(label=label, choices=ROLES)
        elif key == "birth_date":
            field = forms.DateField(label=label, required=False, input_formats=["%Y-%m-%d"],
                                    widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
        elif key == "email":
            field = forms.EmailField(label=label, required=False, max_length=254)
        elif key in ("area", "share_amount"):
            field = forms.DecimalField(label=label, required=False, max_digits=18, decimal_places=2, min_value=0)
        elif key == "represents":
            field = forms.ChoiceField(label=label, required=False, choices=[("", "No representation")] + [
                (item["id"], item.get("name") or item["id"]) for item in case["participants"] if item["id"] != record_id])
        if key in locked:
            # Missing source-bound fields are handled by the existing extraction
            # approval policy, not by an enrichment form.
            field.disabled, field.required = True, False
            field.help_text = "Maintained in Extracted data." if key != "role" else "Primary workflow role."
        field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
        form.fields[key] = field
    if matter.is_locked:
        for field in form.fields.values():
            field.disabled = True
    return form

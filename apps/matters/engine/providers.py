"""Extraction providers.

A provider looks at one document and proposes candidate values for each field
definition. Providers never decide what is true — aggregation, validation and
the review policy do. Both providers return the same Candidate shape, so the
review, conflict and audit logic are identical whichever engine runs.
"""
import json
import logging
import math
import os
import re
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from .normalize import normalize

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    field_key: str
    value: str
    normalized: str
    confidence: Decimal
    attachment: object
    span_start: int
    span_end: int
    method: str
    anchor: str = ""


class RuleProvider:
    """Anchor-label extraction. Runs entirely inside the tenant boundary."""

    name = "rules"
    label = "LexFlow rules engine (local)"

    def extract(self, attachment, text, definitions):
        found = []
        line_starts = [0]
        for m in re.finditer("\n", text):
            line_starts.append(m.end())
        lines = text.split("\n")
        for fdef in definitions:
            candidates = self._find_anchor(fdef, text, lines, line_starts, attachment)
            if not candidates and fdef.data_type == "grundbuch":
                candidate = self._find_pattern(fdef, text, attachment)
                candidates = [candidate] if candidate else []
            found.extend(candidates)
        return found

    def _find_anchor(self, fdef, text, lines, line_starts, attachment):
        found = {}
        for anchor in sorted(fdef.anchors, key=len, reverse=True):
            a = anchor.casefold().rstrip(":")
            if not a:
                continue
            for idx, line in enumerate(lines):
                low = line.casefold()
                pos = low.find(a)
                while pos != -1:
                    if pos == 0 or not low[pos - 1].isalnum():
                        cand = self._candidate_after(
                            fdef, anchor, text, lines, line_starts, idx, pos + len(a), attachment
                        )
                        if cand:
                            key = (cand.span_start, cand.span_end, cand.normalized.casefold())
                            if key not in found or cand.confidence > found[key].confidence:
                                found[key] = cand
                    pos = low.find(a, pos + 1)
        return list(found.values())

    def _candidate_after(self, fdef, anchor, text, lines, line_starts, idx, col, attachment):
        line = lines[idx]
        rest = line[col:]
        colon = re.match(r"\s*[:：]\s*", rest)
        if colon:
            offset = col + colon.end()
            base = Decimal("0.96")
        elif rest[:1] in (" ", "\t"):
            stripped = len(rest) - len(rest.lstrip())
            offset = col + stripped
            base = Decimal("0.88")
        else:
            return None
        raw = line[offset:]
        abs_offset = line_starts[idx] + offset
        if not raw.strip() and idx + 1 < len(lines):
            nxt = lines[idx + 1]
            lead = len(nxt) - len(nxt.lstrip())
            raw = nxt[lead:]
            abs_offset = line_starts[idx + 1] + lead
            base -= Decimal("0.04")
        if not raw.strip():
            return None
        parsed = normalize(fdef.data_type, raw)
        if parsed is None:
            return None
        matched, rel, value = parsed
        start = abs_offset + rel
        return Candidate(
            fdef.key, matched.strip(), value, base, attachment, start, start + len(matched.strip()),
            "anchor", anchor,
        )

    def _find_pattern(self, fdef, text, attachment):
        parsed = normalize(fdef.data_type, text)
        if parsed is None:
            return None
        matched, start, value = parsed
        return Candidate(
            fdef.key, matched.strip(), value, Decimal("0.80"), attachment, start, start + len(matched.strip()),
            "pattern",
        )


class AnthropicProvider:
    """LLM extraction via the Anthropic Messages API (opt-in per tenant).

    The model must quote the exact source text for every value. Quotes are
    located in the document to produce highlight spans; a value whose quote
    cannot be found is discarded before aggregation.
    """

    name = "anthropic"
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or settings.LEXFLOW_ANTHROPIC_MODEL
        self.label = f"Anthropic {self.model}"

    def _tool(self, definitions):
        props = {
            d.key: {
                "type": "object",
                "description": f"{d.label} ({d.get_data_type_display()})",
                "properties": {
                    "value": {"type": "string"},
                    "source_quote": {"type": "string", "description": "Exact text copied from the document"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["value", "source_quote", "confidence"],
            }
            for d in definitions
        }
        return {
            "name": "record_fields",
            "description": "Record values found in the document. Omit any field that is not present.",
            "input_schema": {"type": "object", "properties": props},
        }

    def _call_api(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode(),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    def extract(self, attachment, text, definitions):
        if not text.strip():
            return []
        payload = {
            "model": self.model,
            "max_tokens": 1500,
            "system": (
                "You extract data from German notarial documents. Only report values that are written in the "
                "document. Copy source_quote verbatim. Never guess."
            ),
            "tools": [self._tool(definitions)],
            "tool_choice": {"type": "tool", "name": "record_fields"},
            "messages": [{"role": "user", "content": f"Document '{attachment.display_name}':\n\n{text[:40000]}"}],
        }
        data = self._call_api(payload)
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if not block:
            return []
        out = []
        by_key = {d.key: d for d in definitions}
        for key, item in (block.get("input") or {}).items():
            fdef = by_key.get(key)
            if not fdef or not isinstance(item, dict):
                continue
            quote = (item.get("source_quote") or "").strip()
            try:
                score = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score):
                continue
            raw_conf = Decimal(str(min(max(score, 0.0), 0.97)))
            start = text.find(quote) if quote else -1
            if start < 0:
                continue  # Unsupported values must never become evidence.
            parsed = normalize(fdef.data_type, quote)
            if parsed is None:
                continue
            confidence = raw_conf
            end = start + len(quote)
            out.append(Candidate(key, quote or parsed[0], parsed[2], confidence.quantize(Decimal("0.0001")),
                                 attachment, start, end, "llm"))
        return out


def get_provider(tenant):
    """Pick the engine. External AI is used only when the tenant allows it."""
    wanted = settings.LEXFLOW_AI_PROVIDER
    if wanted == "anthropic" and tenant.allow_external_ai and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    return RuleProvider()

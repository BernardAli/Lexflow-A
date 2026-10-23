import re
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.core.utils import REF_ALPHABET, format_eur, make_ref, money
from apps.matters.engine.aggregate import decide
from apps.matters.engine.normalize import parse_date, parse_grundbuch, parse_money, parse_name
from apps.matters.engine.providers import Candidate


class NormaliseTests(SimpleTestCase):
    def test_german_and_english_money(self):
        self.assertEqual(parse_money("850.000,00 EUR")[2], "850000.00")
        self.assertEqual(parse_money("€850,000")[2], "850000.00")
        self.assertEqual(parse_money("EUR 1.250.000")[2], "1250000.00")
        self.assertEqual(parse_money("25.000,5 Euro")[2], "25000.50")
        self.assertIsNone(parse_money("keine Angabe"))

    def test_dates(self):
        self.assertEqual(parse_date("12.03.1985")[2], "1985-03-12")
        self.assertEqual(parse_date("am 1. März 2026 übergeben")[2], "2026-03-01")
        self.assertIsNone(parse_date("31.02.2020"))

    def test_grundbuch(self):
        self.assertEqual(parse_grundbuch("Im Grundbuch von München Blatt 812 ist")[2], "München Blatt 812")
        self.assertEqual(parse_grundbuch("Freising, Blatt Nr. 2290")[2], "Freising Blatt 2290")

    def test_name_stops_at_details(self):
        self.assertEqual(parse_name("Anna Schmidt, geb. 12.03.1985, wohnhaft …")[2], "Anna Schmidt")
        self.assertEqual(parse_name("Isarwerk Software GmbH")[2], "Isarwerk Software GmbH")
        self.assertIsNone(parse_name("ledig"))


class MoneyAndRefTests(SimpleTestCase):
    def test_money_is_decimal_half_up(self):
        self.assertEqual(money("10.005"), Decimal("10.01"))
        self.assertEqual(money(3), Decimal("3.00"))
        self.assertEqual(format_eur("850000"), "€850,000.00")
        with self.assertRaises(ValueError):
            money("abc")

    def test_reference_format(self):
        ref = make_ref("MAT")
        self.assertRegex(ref, r"^MAT-\d{2}-[%s]{6}$" % re.escape(REF_ALPHABET))
        self.assertFalse(set("01IO") & set(ref[7:]))


def _fdef(**kw):
    base = dict(key="grundbuch", label="Land register", required=True, always_review=False,
                auto_threshold=Decimal("0.90"))
    base.update(kw)
    return SimpleNamespace(**base)


def _cand(norm, conf, name="doc.pdf"):
    return Candidate("grundbuch", norm, norm, Decimal(conf), SimpleNamespace(display_name=name), 0, len(norm), "anchor")


class AggregateTests(SimpleTestCase):
    def test_agreement_raises_confidence(self):
        d = decide(_fdef(), [_cand("München Blatt 812", "0.88"), _cand("München Blatt 812", "0.88")])
        self.assertEqual(d.status, "auto")
        self.assertGreater(d.confidence, Decimal("0.98"))

    def test_disagreement_is_a_conflict(self):
        d = decide(_fdef(), [_cand("München Blatt 812", "0.96", "a.pdf"), _cand("München Blatt 812", "0.96", "b.pdf"),
                             _cand("München Blatt 872", "0.96", "c.pdf")])
        self.assertEqual(d.status, "conflict")
        self.assertEqual(d.normalized, "München Blatt 812")
        self.assertLess(d.confidence, Decimal("0.70"))
        self.assertIn("c.pdf states München Blatt 872", d.note)

    def test_missing_and_optional(self):
        self.assertEqual(decide(_fdef(), []).status, "missing")
        self.assertEqual(decide(_fdef(required=False), []).status, "auto")

    def test_policy_and_threshold(self):
        self.assertEqual(decide(_fdef(always_review=True), [_cand("x Blatt 1", "0.99")]).status, "review")
        self.assertEqual(decide(_fdef(), [_cand("x Blatt 1", "0.80")]).status, "review")

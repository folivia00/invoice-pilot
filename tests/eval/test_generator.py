"""Generator determinism and helpers (no rendering, fast)."""

import json
import random
from datetime import date
from decimal import Decimal

from evalgen import REFERENCE_DATE
from evalgen.content import build_all, ground_truth_payload, load_locales
from evalgen.dataset import dump_json
from evalgen.identifiers import (
    IBAN_COUNTRIES,
    VAT_COUNTRIES,
    corrupt_iban_check_digits,
    make_bad_vat_id,
    make_iban,
    make_vat_id,
)
from evalgen.plan import build_plan
from evalgen.pricing import CURRENCY_SCALE, price_bounds, quantity, unit_price
from invoice_pilot.domain import ValidationStatus, validate
from invoice_pilot.domain.constants import quantize_to
from invoice_pilot.domain.currencies import minor_units
from invoice_pilot.domain.iban import check_iban
from invoice_pilot.domain.tax_ids import TaxIdStatus, check_tax_id

SEED = 20261023


def test_content_generation_is_deterministic() -> None:
    plans = build_plan()
    first = dump_json([ground_truth_payload(c) for c in build_all(SEED, plans)])
    second = dump_json([ground_truth_payload(c) for c in build_all(SEED, plans)])
    assert first == second
    other = dump_json([ground_truth_payload(c) for c in build_all(SEED + 1, plans)])
    assert other != first


def test_plan_is_fixed_and_balanced() -> None:
    plans = build_plan()
    assert len(plans) == 120
    assert [p.doc_id for p in plans][:3] == ["inv-001", "inv-002", "inv-003"]
    assert sum(1 for p in plans if p.corruption) == 15
    assert sum(1 for p in plans if p.two_pages) == 8
    assert sum(1 for p in plans if p.jpy) == 4


def test_generated_content_satisfies_validation() -> None:
    plans = build_plan()
    today = date.fromisoformat(REFERENCE_DATE)
    for content in build_all(SEED, plans):
        report = validate(content.invoice, today=today)
        if content.plan.corruption is None:
            assert report.errors == [], content.plan.doc_id
        else:
            assert report.status is ValidationStatus.NEEDS_REVIEW
            assert set(content.expected_issue_codes) <= {i.code.value for i in report.issues}


def test_ground_truth_json_is_canonical() -> None:
    text = dump_json({"b": 1, "a": {"d": "x", "c": None}})
    assert text == '{\n  "a": {\n    "c": null,\n    "d": "x"\n  },\n  "b": 1\n}\n'
    assert json.loads(text)["a"]["c"] is None


def test_identifier_helpers() -> None:
    rng = random.Random(1)
    for country in IBAN_COUNTRIES:
        iban = make_iban(rng, country)
        assert check_iban(iban).ok
        assert not check_iban(corrupt_iban_check_digits(rng, iban)).checksum_ok
    for country in VAT_COUNTRIES:
        assert check_tax_id(make_vat_id(rng, country)) is TaxIdStatus.VALID
        assert check_tax_id(make_bad_vat_id(rng, country)) is TaxIdStatus.INVALID


def test_templates_use_serif_and_unit_columns() -> None:
    from evalgen.layout import SERIF_TEMPLATES, STYLES, UNIT_COLUMN_TEMPLATES

    assert len(STYLES) == 10
    assert 3 <= len(SERIF_TEMPLATES) <= 4
    assert len(UNIT_COLUMN_TEMPLATES) >= 4


def test_catalog_price_ranges_match_across_languages() -> None:
    locales = load_locales()
    en, de = locales["en"]["items"], locales["de"]["items"]
    assert len(en) == len(de) == 38
    for a, b in zip(en, de, strict=True):
        key = ("unit", "price_min", "price_max", "qty_max")
        assert tuple(a.get(k) for k in key) == tuple(b.get(k) for k in key), (a, b)
        assert 0 < a["price_min"] < a["price_max"]
        assert (a["unit"] in ("weight", "length")) == ("qty_max" in a), a
        if "qty_max" in a:
            assert a["qty_max"] >= 10


def test_unit_price_and_quantity_rules() -> None:
    rng = random.Random(7)
    for entry in load_locales()["en"]["items"]:
        for currency in CURRENCY_SCALE:
            units = minor_units(currency)
            assert units is not None
            low, high = price_bounds(entry, currency, units)
            assert Decimal(str(entry["price_min"])) * CURRENCY_SCALE[currency] <= low
            assert high <= Decimal(str(entry["price_max"])) * CURRENCY_SCALE[currency]
            for _ in range(40):
                price = unit_price(rng, entry, currency, units)
                assert low <= price <= high and price == quantize_to(price, units)
        for _ in range(200):
            qty = quantity(rng, entry)
            if entry["unit"] == "piece":
                assert qty == qty.to_integral_value() and 1 <= qty <= 50
            elif entry["unit"] == "hour":
                assert (qty * 4) == (qty * 4).to_integral_value() and Decimal("0.5") <= qty <= 80
            elif entry["unit"] == "flat":
                assert qty == 1
            else:
                assert 1 <= qty <= entry["qty_max"] and -qty.as_tuple().exponent <= 2

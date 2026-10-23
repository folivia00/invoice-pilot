"""Dataset composition: which document gets which input type, language, template, corruption.

Everything here is a pure function of the document index, so the composition
is fixed by this prompt (task 06) and independent of the random seed. The seed
only drives the content (names, amounts, dates, identifiers).
"""

from dataclasses import dataclass
from typing import Final

TOTAL_DOCS: Final[int] = 120
TEMPLATES: Final[tuple[str, ...]] = tuple(f"t{i:02d}" for i in range(1, 11))

#: input type -> (number of EN docs, number of DE docs); sums: 48 / 36 / 36, EN 72, DE 48.
COMPOSITION: Final[dict[str, tuple[int, int]]] = {
    "text_pdf": (29, 19),
    "scan_pdf": (22, 14),
    "photo": (21, 15),
}

CORRUPTION_KINDS: Final[tuple[str, ...]] = (
    "total_mismatch",
    "line_amount_mismatch",
    "subtotal_mismatch",
    "missing_invoice_number",
    "bad_iban_check_digits",
    "due_before_invoice_date",
    "bad_vat_format",
)
#: 15 corrupted documents spread over input types and languages
#: (text_pdf 4 EN + 2 DE, scan_pdf 3 EN + 2 DE, photo 2 EN + 2 DE); kinds cycle in index order.
CORRUPTED_INDICES: Final[tuple[int, ...]] = (
    7, 9, 14, 23, 28, 36, 37, 51, 66, 67, 70, 74, 83, 87, 109,
)  # fmt: skip

#: Two-page documents (6-8 line items, short first page). Only PDFs: a photo is one page.
TWO_PAGE_INDICES: Final[tuple[int, ...]] = (3, 19, 34, 45, 61, 76, 91, 103)

#: Documents invoiced in JPY (EN language, Japanese supplier).
JPY_INDICES: Final[tuple[int, ...]] = (5, 29, 57, 94)


@dataclass(frozen=True, slots=True)
class DocPlan:
    """Fixed composition attributes of one document."""

    index: int
    doc_id: str
    input_type: str
    language: str
    template: str
    corruption: str | None
    two_pages: bool
    jpy: bool


def _slots() -> list[tuple[str, str]]:
    """Return 120 (input_type, language) pairs interleaved so types and languages mix."""
    pools = {
        f"{kind}:{lang}": count
        for kind, (en, de) in COMPOSITION.items()
        for lang, count in (("en", en), ("de", de))
    }
    order = list(pools)
    slots: list[tuple[str, str]] = []
    # Round-robin over the six pools, proportional to their remaining counts.
    remaining = dict(pools)
    while len(slots) < TOTAL_DOCS:
        for key in order:
            if remaining[key] <= 0:
                continue
            kind, lang = key.split(":")
            slots.append((kind, lang))
            remaining[key] -= 1
            if len(slots) == TOTAL_DOCS:
                break
    return slots


def build_plan() -> list[DocPlan]:
    """Return the fixed plan for all 120 documents."""
    slots = _slots()
    # Make sure two-page and JPY documents land on allowed slots.
    plans: list[DocPlan] = []
    for index, (kind, lang) in enumerate(slots, start=1):
        plans.append(
            DocPlan(
                index=index,
                doc_id=f"inv-{index:03d}",
                input_type=kind,
                language=lang,
                template=TEMPLATES[(index - 1) % len(TEMPLATES)],
                corruption=_corruption_for(index),
                two_pages=index in TWO_PAGE_INDICES,
                jpy=index in JPY_INDICES,
            )
        )
    _check_plan(plans)
    return plans


def _corruption_for(index: int) -> str | None:
    if index not in CORRUPTED_INDICES:
        return None
    position = CORRUPTED_INDICES.index(index)
    return CORRUPTION_KINDS[position % len(CORRUPTION_KINDS)]


def _check_plan(plans: list[DocPlan]) -> None:
    assert len(plans) == TOTAL_DOCS
    for plan in plans:
        if plan.two_pages:
            assert plan.input_type != "photo", plan
        if plan.jpy:
            assert plan.language == "en", plan
            assert plan.corruption not in ("bad_iban_check_digits", "bad_vat_format"), plan
    for kind, (en, de) in COMPOSITION.items():
        assert sum(1 for p in plans if p.input_type == kind and p.language == "en") == en
        assert sum(1 for p in plans if p.input_type == kind and p.language == "de") == de
    assert sum(1 for p in plans if p.corruption) == len(CORRUPTED_INDICES)

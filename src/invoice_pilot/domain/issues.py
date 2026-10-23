"""Validation outcome types: issue codes, severities, report."""

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    """How an issue affects the final status."""

    ERROR = "error"
    WARNING = "warning"


class ValidationStatus(Enum):
    """Final decision for an invoice."""

    AUTO_APPROVED = "auto_approved"
    NEEDS_REVIEW = "needs_review"


class IssueCode(Enum):
    """Stable string codes; eval metrics are counted by these values."""

    MISSING_FIELD = "missing_field"
    INVALID_CURRENCY = "invalid_currency"
    NEGATIVE_VALUE = "negative_value"
    LINE_AMOUNT_MISMATCH = "line_amount_mismatch"
    SUBTOTAL_MISMATCH = "subtotal_mismatch"
    TOTAL_MISMATCH = "total_mismatch"
    TAX_AMOUNT_MISMATCH = "tax_amount_mismatch"
    TAX_RATE_OUT_OF_RANGE = "tax_rate_out_of_range"
    ZERO_TOTAL = "zero_total"
    DUE_BEFORE_INVOICE_DATE = "due_before_invoice_date"
    INVOICE_DATE_IN_FUTURE = "invoice_date_in_future"
    INVALID_IBAN = "invalid_iban"
    IBAN_LENGTH_UNVERIFIED = "iban_length_unverified"
    INVALID_TAX_ID = "invalid_tax_id"
    TAX_ID_UNVERIFIED = "tax_id_unverified"


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding of the validation rules."""

    code: IssueCode
    field: str
    severity: Severity
    message: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of ``validate``: status plus the complete list of issues."""

    status: ValidationStatus
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def codes(self) -> set[IssueCode]:
        return {i.code for i in self.issues}

    def has(self, code: IssueCode, field_path: str | None = None) -> bool:
        """Return True if an issue with ``code`` (and optionally ``field_path``) exists."""
        return any(
            i.code is code and (field_path is None or i.field == field_path) for i in self.issues
        )

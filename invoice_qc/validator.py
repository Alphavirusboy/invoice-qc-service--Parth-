"""Validation engine applying completeness, format, business, and anomaly rules."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Iterable, List, Tuple

from .schemas import Invoice, InvoiceValidationResult, ValidationResponse, ValidationSummary
from .utils import ALLOWED_CURRENCIES, approx_equal, non_negative, parse_date, safe_decimal


class InvoiceValidator:
    def __init__(self, allowed_currencies: Iterable[str] = ALLOWED_CURRENCIES, tolerance: Decimal = Decimal("0.02")) -> None:
        self.allowed_currencies = set(allowed_currencies)
        self.tolerance = tolerance

    def validate_invoices(self, invoices: List[Invoice]) -> ValidationResponse:
        seen_keys: set[Tuple[str, str, str]] = set()
        results: List[InvoiceValidationResult] = []
        error_counter: Counter[str] = Counter()

        for invoice in invoices:
            errors: list[str] = []
            warnings: list[str] = []

            # Duplicate detection
            key = invoice.key()
            if any(key):
                if key in seen_keys:
                    errors.append("anomaly: duplicate_invoice")
                else:
                    seen_keys.add(key)

            # Completeness
            if not invoice.invoice_number:
                errors.append("missing_field: invoice_number")
            if not invoice.invoice_date:
                errors.append("missing_field: invoice_date")
            if not invoice.seller_name:
                errors.append("missing_field: seller_name")
            if not invoice.buyer_name:
                errors.append("missing_field: buyer_name")

            # Date parsing and ordering
            parsed_invoice_date = parse_date(invoice.invoice_date) if invoice.invoice_date else None
            if invoice.invoice_date and not parsed_invoice_date:
                errors.append("format: invoice_date_unparseable")
            parsed_due_date = parse_date(invoice.due_date) if invoice.due_date else None
            if invoice.due_date and not parsed_due_date:
                errors.append("format: due_date_unparseable")
            if parsed_invoice_date and parsed_due_date and parsed_due_date < parsed_invoice_date:
                errors.append("business: due_before_invoice_date")

            # Currency
            if invoice.currency:
                if invoice.currency not in self.allowed_currencies:
                    errors.append("format: currency_unknown")
            else:
                errors.append("missing_field: currency")

            # Money fields
            net = safe_decimal(invoice.net_total)
            tax = safe_decimal(invoice.tax_amount)
            gross = safe_decimal(invoice.gross_total)
            if invoice.net_total is not None and net is None:
                errors.append("format: net_total_not_numeric")
            if invoice.tax_amount is not None and tax is None:
                errors.append("format: tax_amount_not_numeric")
            if invoice.gross_total is not None and gross is None:
                errors.append("format: gross_total_not_numeric")

            if not non_negative(net):
                errors.append("business: net_total_negative")
            if not non_negative(tax):
                errors.append("business: tax_amount_negative")
            if not non_negative(gross):
                errors.append("business: gross_total_negative")

            # Business totals consistency
            if net is not None and tax is not None and gross is not None:
                if not approx_equal(net + tax, gross, tolerance=self.tolerance):
                    errors.append("business: totals_mismatch")

            if invoice.line_items:
                line_sum = sum([safe_decimal(li.line_total) or Decimal(0) for li in invoice.line_items])
                if net is not None and not approx_equal(line_sum, net, tolerance=self.tolerance):
                    errors.append("business: line_items_sum_mismatch")
                if line_sum > Decimal(0) and (net is None or net == Decimal(0)):
                    warnings.append("anomaly: zero_net_with_line_items")

            result = InvoiceValidationResult(
                invoice_id=invoice.display_id,
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
            )
            results.append(result)
            error_counter.update(errors)

        summary = ValidationSummary(
            total_invoices=len(results),
            valid_invoices=sum(1 for r in results if r.is_valid),
            invalid_invoices=sum(1 for r in results if not r.is_valid),
            error_counts=dict(error_counter),
        )
        return ValidationResponse(summary=summary, results=results)

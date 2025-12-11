"""PDF to structured invoice extraction module."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import List, Optional

import pdfplumber

from .schemas import Invoice, LineItem
from .utils import ALLOWED_CURRENCIES


INVOICE_NO_PATTERNS = [
    r"Invoice\s*(No\.|#)?\s*[:\-]?\s*([A-Za-z0-9\-_/]+)",
    r"Invoice\s*Number\s*[:\-]?\s*([A-Za-z0-9\-_/]+)",
]
INVOICE_DATE_PATTERNS = [
    r"Invoice\s*Date\s*[:\-]?\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})",
    r"Date\s*[:\-]?\s*([0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})",
]
DUE_DATE_PATTERNS = [
    r"Due\s*Date\s*[:\-]?\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})",
]
CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "₹": "INR", "£": "GBP"}
AMOUNT_LABELS = {
    "net_total": ["Subtotal", "Sub Total", "Net Total", "Net Amount"],
    "tax_amount": ["VAT", "GST", "Tax", "Tax Amount"],
    "gross_total": ["Total", "Grand Total", "Invoice Total", "Amount Due"],
}


class InvoiceExtractor:
    """Extract invoice fields from PDFs using lightweight heuristics."""

    def __init__(self, currency_fallback: str = "EUR") -> None:
        self.currency_fallback = currency_fallback

    # Public API
    def extract_from_dir(self, pdf_dir: str | Path) -> List[Invoice]:
        pdf_dir = Path(pdf_dir)
        invoices: List[Invoice] = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            invoices.append(self.extract_from_pdf(pdf_path))
        return invoices

    def extract_from_pdf(self, pdf_path: str | Path) -> Invoice:
        text = self._read_pdf_text(Path(pdf_path))
        return self.parse_text(text, source_name=Path(pdf_path).name)

    def extract_from_bytes(self, file_bytes: bytes, source_name: Optional[str] = None) -> Invoice:
        text = self._read_pdf_bytes(file_bytes)
        return self.parse_text(text, source_name=source_name)

    def parse_text(self, text: str, source_name: Optional[str] = None) -> Invoice:
        normalized = text.replace("\r", "")
        invoice = Invoice()

        invoice.invoice_number = self._first_match(INVOICE_NO_PATTERNS, normalized, group=2)
        invoice.invoice_date = self._first_match(INVOICE_DATE_PATTERNS, normalized)
        invoice.due_date = self._first_match(DUE_DATE_PATTERNS, normalized)

        invoice.currency = self._detect_currency(normalized)
        invoice.payment_terms = self._maybe_match(r"(Net\s+\d+|Payment\s+Terms[:\-]?\s*[A-Za-z0-9 ]+)", normalized)

        invoice.seller_name = self._maybe_match(r"Seller\s*[:\-]?\s*(.+)", normalized)
        invoice.buyer_name = self._maybe_match(r"Buyer\s*[:\-]?\s*(.+)", normalized)

        invoice.net_total = self._find_amount_for_labels(normalized, AMOUNT_LABELS["net_total"])
        invoice.tax_amount = self._find_amount_for_labels(normalized, AMOUNT_LABELS["tax_amount"])
        invoice.gross_total = self._find_amount_for_labels(normalized, AMOUNT_LABELS["gross_total"])

        invoice.line_items = self._extract_line_items(normalized)
        invoice.notes = self._collect_notes(normalized)

        if source_name and not invoice.external_reference:
            invoice.external_reference = source_name
        return invoice

    def export_json(self, invoices: List[Invoice], output_path: str | Path) -> None:
        path = Path(output_path)
        serializable = [inv.model_dump() for inv in invoices]
        path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    # Internals
    def _read_pdf_text(self, pdf_path: Path) -> str:
        pages_text: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)

    def _read_pdf_bytes(self, file_bytes: bytes) -> str:
        pages_text: list[str] = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        return "\n".join(pages_text)

    def _first_match(self, patterns: List[str], text: str, group: int = 1) -> Optional[str]:
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m and m.group(group):
                return m.group(group).strip()
        return None

    def _maybe_match(self, pattern: str, text: str, group: int = 1) -> Optional[str]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and m.group(group):
            return m.group(group).strip()
        return None

    def _detect_currency(self, text: str) -> Optional[str]:
        for symbol, code in CURRENCY_SYMBOLS.items():
            if symbol in text:
                return code
        code_match = re.search(r"\b(EUR|USD|GBP|INR)\b", text)
        if code_match:
            return code_match.group(1)
        return self.currency_fallback if self.currency_fallback in ALLOWED_CURRENCIES else None

    def _find_amount_for_labels(self, text: str, labels: List[str]) -> Optional[float]:
        for label in labels:
            pattern = rf"{re.escape(label)}[^\d\-]*(-?\d+[\d.,]*)"
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                return self._to_number(m.group(1))
        return None

    def _to_number(self, value: str) -> Optional[float]:
        try:
            cleaned = value.replace(",", "").strip()
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    def _extract_line_items(self, text: str) -> List[LineItem]:
        items: List[LineItem] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            m = re.match(r"(.+?)\s+(\d+[\d.,]*)\s+(\d+[\d.,]*)\s+(\d+[\d.,]*)$", line)
            if m:
                description = m.group(1).strip()
                qty = self._to_number(m.group(2))
                unit_price = self._to_number(m.group(3))
                line_total = self._to_number(m.group(4))
                if description and (qty or unit_price or line_total):
                    items.append(
                        LineItem(
                            description=description,
                            quantity=qty,
                            unit_price=unit_price,
                            line_total=line_total,
                        )
                    )
        return items

    def _collect_notes(self, text: str) -> Optional[str]:
        footer_match = re.search(r"Notes?[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
        if footer_match:
            return footer_match.group(1).strip()
        return None
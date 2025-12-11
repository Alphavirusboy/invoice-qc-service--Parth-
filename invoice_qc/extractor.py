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
PO_NO_PATTERNS = [
    r"(?:Bestellung|Order)\s+AUFNR\s*([A-Za-z0-9\-_/]+)",
    r"(?:AUFNR)\s*[:\-]?\s*([A-Za-z0-9\-_/]+)",
]
INVOICE_DATE_PATTERNS = [
    r"Invoice\s*Date\s*[:\-]?\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})",
    r"Date\s*[:\-]?\s*([0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})",
]
PO_DATE_PATTERNS = [
    r"(?:Bestellung|Order)\s+AUFNR\d+\s+vom\s+([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})",
    r"Bestellung\s+AUFNR[A-Za-z0-9\-_/]+\s+vom\s+([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})",
]
DUE_DATE_PATTERNS = [
    r"Due\s*Date\s*[:\-]?\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})",
]
CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "₹": "INR", "£": "GBP"}
AMOUNT_LABELS = {
    "net_total": [
        (True, r"Gesamtwert(?!\s+inkl)[^\d\-]*(-?\d+[\d.,]*)"),  # With capture group
        (False, "Subtotal"), (False, "Sub Total"), (False, "Net Total"), (False, "Net Amount"), (False, "Netto"),
    ],
    "tax_amount": [
        (True, r"MwSt\.\s*[\d.,]*%\s*(?:EUR|€)\s*([\d.,]+)"),  # "MwSt. 19,00% EUR 12,16"
        (False, "MwSt"), (False, "VAT"), (False, "GST"), (False, "Tax"), (False, "Tax Amount"), (False, "Umsatzsteuer"),
    ],
    "gross_total": [
        (True, r"Gesamtwert\s+inkl[^\d\-]*(-?\d+[\d.,]*)"),  # With capture group
        (False, "Total"), (False, "Grand Total"), (False, "Invoice Total"), (False, "Amount Due"), (False, "Gesamtsumme"),
    ],
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

        # Detect document type (Invoice vs Purchase Order)
        is_purchase_order = self._is_purchase_order(normalized)

        # Extract invoice/order number based on type
        if is_purchase_order:
            invoice.invoice_number = self._first_match(PO_NO_PATTERNS, normalized, group=1)
            invoice.invoice_date = self._first_match(PO_DATE_PATTERNS, normalized, group=1)
        else:
            invoice.invoice_number = self._first_match(INVOICE_NO_PATTERNS, normalized, group=2)
            invoice.invoice_date = self._first_match(INVOICE_DATE_PATTERNS, normalized)

        invoice.due_date = self._first_match(DUE_DATE_PATTERNS, normalized)

        invoice.currency = self._detect_currency(normalized)
        invoice.payment_terms = self._maybe_match(r"(Net\s+\d+|Payment\s+Terms[:\-]?\s*[A-Za-z0-9 ]+)", normalized)

        # Extract seller and buyer
        if is_purchase_order:
            # For PO: extract from German labels
            seller_name, buyer_name = self._extract_po_parties(normalized)
            invoice.seller_name = seller_name
            invoice.buyer_name = buyer_name
        else:
            invoice.seller_name = self._maybe_match(r"Seller\s*[:\-]?\s*(.+)", normalized)
            invoice.buyer_name = self._maybe_match(r"Buyer\s*[:\-]?\s*(.+)", normalized)

        invoice.net_total = self._find_amount_for_labels(normalized, AMOUNT_LABELS["net_total"])
        invoice.tax_amount = self._find_amount_for_labels(normalized, AMOUNT_LABELS["tax_amount"])
        invoice.gross_total = self._find_amount_for_labels(normalized, AMOUNT_LABELS["gross_total"])

        invoice.line_items = self._extract_line_items(normalized, is_po=is_purchase_order)
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

    def _find_amount_for_labels(self, text: str, labels: List[tuple]) -> Optional[float]:
        for is_regex, label in labels:
            if is_regex:
                # For regex patterns that already have capture group(s)
                m = re.search(label, text, flags=re.IGNORECASE)
                if m:
                    # Get the last group (or first if only one)
                    group_num = m.lastindex if m.lastindex else 1
                    try:
                        return self._to_number(m.group(group_num))
                    except (IndexError, TypeError):
                        return self._to_number(m.group(1))
            else:
                # Plain text labels - escape and add capture group
                pattern = rf"{re.escape(label)}[^\d\-]*(-?\d+[\d.,]*)"
                m = re.search(pattern, text, flags=re.IGNORECASE)
                if m:
                    return self._to_number(m.group(1))
        return None

    def _to_number(self, value: str) -> Optional[float]:
        try:
            if not value or not isinstance(value, str):
                return None
            
            cleaned = value.strip()
            
            # Handle German format (comma as decimal, optionally period as thousands separator)
            # vs US format (period as decimal, comma as thousands separator)
            # Heuristic: if there's a comma and a period, the rightmost one is the decimal separator
            # If only comma, check if it's German or US format
            
            if "," in cleaned and "." in cleaned:
                # Both present: rightmost is decimal
                if cleaned.rindex(",") > cleaned.rindex("."):
                    # Comma is rightmost: German format (1.234,56)
                    cleaned = cleaned.replace(".", "").replace(",", ".")
                else:
                    # Period is rightmost: US format (1,234.56)
                    cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                # Only comma: German format if 4 or fewer digits after comma, else US format
                parts = cleaned.split(",")
                if len(parts[-1]) <= 4:
                    # German: 16,0000 or 64,00
                    cleaned = cleaned.replace(",", ".")
                # else keep as is (US thousands separator)
                else:
                    cleaned = cleaned.replace(",", "")
            # else only period or no separator
            
            return float(cleaned)
        except (ValueError, AttributeError, IndexError):
            return None

    def _is_purchase_order(self, text: str) -> bool:
        """Detect if document is a purchase order (German Bestellung) vs invoice."""
        return bool(re.search(r"(?:Bestellung|AUFNR|Pos\.\s+Artikelbeschreibung)", text, flags=re.IGNORECASE))

    def _extract_po_parties(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Extract seller and buyer from German purchase order."""
        lines = text.split("\n")
        seller_name = None
        buyer_name = None

        # Look for "Bestellung AUFNR..." line - seller is before this
        # Look for "Kundenanschrift" - buyer is after this
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Try to find seller from first company name at document start
            # (before "Kundenanschrift")
            if "Kundenanschrift" in line and seller_name is None:
                # Look backwards from Kundenanschrift
                for j in range(max(0, i - 5), i):
                    candidate = lines[j].strip()
                    # Filter: must be company-like (> 4 chars, no special keywords)
                    if (candidate and len(candidate) > 4 and 
                        not any(x in candidate.lower() for x in 
                                ["seite", "page", "fax", "telefon", "aufnr", "auftrag", 
                                 "bestellung", "von", "im", "gmbh", "gag"])):
                        seller_name = candidate
                        break

            # Buyer is listed right after "Kundenanschrift"
            if "Kundenanschrift" in line and i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                if candidate and len(candidate) > 4:
                    buyer_name = candidate

            # Fallback: if no seller found yet, look for the corporation name in first line
            if seller_name is None and "Bestellung" in line and "AUFNR" in line:
                # Extract company name from line like "XYZ Corporation Bestellung AUFNR123456 im Auftrag von..."
                m = re.match(r"^([A-Za-z\s]+?)\s+(?:Bestellung|Order)", line)
                if m:
                    seller_name = m.group(1).strip()

        return seller_name, buyer_name

    def _extract_line_items(self, text: str, is_po: bool = False) -> List[LineItem]:
        items: List[LineItem] = []

        if is_po:
            # For German POs: look for "Pos. Artikelbeschreibung ... EUR" section
            # Format spans multiple lines:
            # Line 1: "1 Sterilisationsmittel 4 VE 1 VE=20 Stück 64,00"
            # Line 2-4: Details like "Lief.Art.Nr:", "Interne Mat.Nr: ... 16,0000 pro 1 VE", etc.
            lines = text.split("\n")
            in_items_section = False
            current_item = None

            for i, line in enumerate(lines):
                stripped = line.strip()

                # Detect start of items section
                if "Pos." in line and "Artikelbeschreibung" in line:
                    in_items_section = True
                    continue

                if not in_items_section:
                    continue

                # Stop at totals section
                if any(x in stripped.lower() for x in ["gesamtwert", "mwst", "summe"]):
                    break

                # Try to parse main line: "Pos_num Description Qty Unit LineTotal"
                m = re.match(r"^\d+\s+(.+?)\s+(\d+(?:[\d.,]*)?)\s+.+?\s+([\d.,]+)$", stripped)
                if m:
                    description = m.group(1).strip()
                    qty = self._to_number(m.group(2))
                    line_total = self._to_number(m.group(3))

                    if description and len(description) > 2 and (qty or line_total):
                        current_item = {
                            "description": description,
                            "quantity": qty,
                            "unit_price": None,  # May be found on next line
                            "line_total": line_total,
                        }
                # Else try to find unit price in detail line (e.g., "Interne Mat.Nr: ... 16,0000 pro 1 VE")
                elif current_item and "pro" in stripped:
                    # Pattern: "... NUMBER pro 1 UNIT"
                    m = re.search(r"([\d.,]+)\s+pro\s+\d+\s+\w+", stripped)
                    if m:
                        current_item["unit_price"] = self._to_number(m.group(1))
                        # Now finalize item
                        items.append(
                            LineItem(
                                description=current_item["description"],
                                quantity=current_item["quantity"],
                                unit_price=current_item["unit_price"],
                                line_total=current_item["line_total"],
                            )
                        )
                        current_item = None
                # Also try pattern where detail line has Material info: "...MatNr: 49115 16,0000 pro..."
                elif current_item and "Mat.Nr" in stripped or "Mat Nr" in stripped:
                    m = re.search(r"\d+\s+([\d.,]+)\s+pro", stripped)
                    if m:
                        current_item["unit_price"] = self._to_number(m.group(1))
                # If next item starts before we found unit price, save current and start new
                elif current_item and m and re.match(r"^\d+\s+", stripped):
                    # We hit the next item line. Save current item
                    items.append(
                        LineItem(
                            description=current_item["description"],
                            quantity=current_item["quantity"],
                            unit_price=current_item.get("unit_price"),
                            line_total=current_item.get("line_total"),
                        )
                    )
                    current_item = None

            # Don't forget the last item
            if current_item:
                items.append(
                    LineItem(
                        description=current_item["description"],
                        quantity=current_item["quantity"],
                        unit_price=current_item.get("unit_price"),
                        line_total=current_item.get("line_total"),
                    )
                )
        else:
            # Standard invoice line item parsing
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
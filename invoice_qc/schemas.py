"""Data models used across extractor, validator, CLI, and API."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, ConfigDict


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    tax_rate: Optional[float] = None


class Invoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_number: Optional[str] = None
    external_reference: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    seller_name: Optional[str] = None
    seller_address: Optional[str] = None
    seller_tax_id: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_address: Optional[str] = None
    buyer_tax_id: Optional[str] = None
    currency: Optional[str] = None
    payment_terms: Optional[str] = None
    net_total: Optional[float] = None
    tax_amount: Optional[float] = None
    gross_total: Optional[float] = None
    notes: Optional[str] = None
    line_items: List[LineItem] = Field(default_factory=list)

    def key(self) -> Tuple[str, str, str]:
        """Composite key used for duplicate detection."""
        return (
            self.invoice_number or "",
            self.seller_name or "",
            self.invoice_date or "",
        )

    @property
    def display_id(self) -> str:
        """Fallback identifier for messages and reports."""
        return self.invoice_number or self.external_reference or "<unknown>"


class InvoiceValidationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_id: str
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_invoices: int
    valid_invoices: int
    invalid_invoices: int
    error_counts: Dict[str, int] = Field(default_factory=dict)


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: ValidationSummary
    results: List[InvoiceValidationResult]


class ExtractAndValidateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoices: List[Invoice]
    validation: ValidationResponse

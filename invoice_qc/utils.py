"""Utility functions shared across the invoice QC service."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from dateutil import parser

ALLOWED_CURRENCIES = {"EUR", "USD", "GBP", "INR"}


def parse_date(value: str) -> Optional[date]:
    """Parse a date string into a date object; returns None on failure."""
    if not value:
        return None
    try:
        return parser.parse(value, dayfirst=False, yearfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        try:
            return parser.parse(value, dayfirst=True, yearfirst=True).date()
        except Exception:
            return None


def safe_decimal(value: object) -> Optional[Decimal]:
    """Convert to Decimal if possible, else None."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def approx_equal(a: Optional[Decimal], b: Optional[Decimal], tolerance: Decimal = Decimal("0.02")) -> bool:
    """Check if two money-like values are approximately equal within tolerance.

    Tolerance is absolute by default (e.g., 2 cents). If values are large,
    the caller can pass a larger tolerance if needed.
    """
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


def non_negative(value: Optional[Decimal]) -> bool:
    """Return True if value is None or >= 0."""
    if value is None:
        return True
    return value >= 0

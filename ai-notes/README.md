# AI Usage Notes

- Used GPT-5.1-Codex-Max (Preview) to scaffold FastAPI/CLI structure, regex patterns, and validation rule ideas.
- Manually adjusted regex capture groups to avoid pulling labels; kept tolerance-based money checks to reduce false mismatches.
- Added CORS middleware and frontend error/status handling after manual tests showed browser fetch failures.
- Future improvement: add OCR (e.g., pytesseract) for scanned PDFs; currently supports text-based PDFs only.

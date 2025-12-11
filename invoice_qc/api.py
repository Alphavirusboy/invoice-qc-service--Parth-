"""FastAPI application exposing validation endpoints."""
from __future__ import annotations

from typing import List

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from .extractor import InvoiceExtractor
from .schemas import Invoice, ValidationResponse, ExtractAndValidateResponse
from .validator import InvoiceValidator

app = FastAPI(title="Invoice QC Service", version="0.1.0")

# Add CORS middleware to allow browser requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/validate-json", response_model=ValidationResponse)
def validate_json(invoices: List[Invoice]):
    validator = InvoiceValidator()
    return validator.validate_invoices(invoices)


@app.post("/extract-and-validate-pdfs", response_model=ExtractAndValidateResponse)
async def extract_and_validate_pdfs(files: List[UploadFile] = File(...)):
    extractor = InvoiceExtractor()
    invoices: List[Invoice] = []
    for f in files:
        content = await f.read()
        invoices.append(extractor.extract_from_bytes(content, source_name=f.filename))
    validator = InvoiceValidator()
    validation = validator.validate_invoices(invoices)
    return ExtractAndValidateResponse(invoices=invoices, validation=validation)

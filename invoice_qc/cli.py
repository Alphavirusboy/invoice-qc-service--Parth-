"""Command-line entrypoints for extraction and validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print

from .extractor import InvoiceExtractor
from .schemas import Invoice
from .validator import InvoiceValidator

app = typer.Typer(add_completion=False, help="Invoice QC CLI")


def _load_invoices(json_path: Path) -> list[Invoice]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return [Invoice.model_validate(item) for item in data]


def _print_summary(response) -> None:
    summary = response.summary
    print(f"[bold]Total:[/bold] {summary.total_invoices}")
    print(f"[green]Valid:[/green] {summary.valid_invoices}  [red]Invalid:[/red] {summary.invalid_invoices}")
    if summary.error_counts:
        print("Top errors:")
        for err, count in sorted(summary.error_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"- {err}: {count}")


@app.command()
def extract(pdf_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Folder of invoice PDFs"), output: Path = typer.Option(..., help="Path to write extracted JSON")) -> None:
    """Extract structured data from PDFs into JSON."""
    extractor = InvoiceExtractor()
    invoices = extractor.extract_from_dir(pdf_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    extractor.export_json(invoices, output)
    print(f"Extracted {len(invoices)} invoices -> {output}")


@app.command()
def validate(input: Path = typer.Option(..., exists=True, dir_okay=False, help="JSON file with invoices"), report: Optional[Path] = typer.Option(None, help="Optional path to write validation report")) -> None:
    """Validate invoices in a JSON file."""
    invoices = _load_invoices(input)
    validator = InvoiceValidator()
    response = validator.validate_invoices(invoices)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(response.model_dump_json(indent=2), encoding="utf-8")
        print(f"Report written to {report}")
    _print_summary(response)
    if response.summary.invalid_invoices > 0:
        raise typer.Exit(code=1)


@app.command("full-run")
def full_run(pdf_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Folder of invoice PDFs"), report: Path = typer.Option(..., help="Path to write validation report")) -> None:
    """Extract PDFs then validate them end-to-end."""
    extractor = InvoiceExtractor()
    invoices = extractor.extract_from_dir(pdf_dir)
    validator = InvoiceValidator()
    response = validator.validate_invoices(invoices)

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(response.model_dump_json(indent=2), encoding="utf-8")
    print(f"Report written to {report}")
    _print_summary(response)
    if response.summary.invalid_invoices > 0:
        raise typer.Exit(code=1)


def main():
    app()


if __name__ == "__main__":
    main()

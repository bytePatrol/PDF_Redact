"""PDF Text Redactor — search and permanently redact text in PDF files.

Usage::

    from pdf_redactor import redact_pdf, parse_terms

    terms = parse_terms("secret, confidential")
    result = redact_pdf(Path("input.pdf"), terms)
    print(f"Redacted {result.total_matches} matches -> {result.output_path}")

Or run the GUI::

    python -m pdf_redactor
"""

from pdf_redactor.redactor import RedactionResult, parse_terms, redact_pdf

__all__ = ["RedactionResult", "parse_terms", "redact_pdf"]

"""PDF text redaction engine.

Provides functions to search for and permanently redact text in PDF files
using black-filled rectangles. The redacted text is irrecoverably removed
from the document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Default redaction fill color (black).
DEFAULT_FILL_COLOR: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class RedactionResult:
    """Summary of a completed redaction operation.

    Attributes:
        output_path: Where the redacted PDF was saved.
        total_matches: Total number of text matches redacted across all pages.
        matches_per_term: Count of matches found for each search term.
        pages_modified: Number of pages that contained at least one match.
        pages_total: Total number of pages in the document.
    """

    output_path: Path
    total_matches: int
    matches_per_term: dict[str, int]
    pages_modified: int
    pages_total: int
    terms_not_found: list[str] = field(default_factory=list)


def parse_terms(raw_input: str) -> list[str]:
    """Parse a raw string of redaction terms into a deduplicated list.

    Terms can be separated by newlines or commas. Leading/trailing whitespace
    is stripped from each term. Empty terms and duplicates are removed.

    Args:
        raw_input: Raw text containing terms separated by newlines or commas.

    Returns:
        Ordered list of unique, non-empty terms.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for line in raw_input.splitlines():
        for part in line.split(","):
            term = part.strip()
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
    return terms


def redact_pdf(
    input_path: Path,
    terms: list[str],
    output_path: Path | None = None,
    fill_color: tuple[float, float, float] = DEFAULT_FILL_COLOR,
    progress_callback: Callable[[int, int], None] | None = None,
) -> RedactionResult:
    """Search for and permanently redact all occurrences of terms in a PDF.

    Each match is covered with a filled rectangle and the underlying text is
    removed from the document. This operation is irreversible once saved.

    Args:
        input_path: Path to the source PDF file.
        terms: List of text strings to search for and redact.
        output_path: Where to save the redacted PDF. Defaults to
            ``<original_stem>_redacted.pdf`` in the same directory.
        fill_color: RGB fill color for redaction boxes, each component 0.0-1.0.
            Defaults to black ``(0, 0, 0)``.
        progress_callback: Optional callable invoked after each page is
            processed, receiving ``(current_page, total_pages)`` (1-indexed).

    Returns:
        A ``RedactionResult`` summarizing what was redacted and where the
        output was saved.

    Raises:
        FileNotFoundError: If ``input_path`` does not exist.
        ValueError: If ``terms`` is empty.
        fitz.FileDataError: If the file is not a valid PDF.
        PermissionError: If the output file cannot be written.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"PDF not found: {input_path}")
    if not terms:
        raise ValueError("At least one redaction term is required.")

    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}_redacted.pdf")
    output_path = Path(output_path)

    matches_per_term: dict[str, int] = {term: 0 for term in terms}
    pages_modified = 0

    logger.info("Opening PDF: %s (%d terms to redact)", input_path, len(terms))

    doc: fitz.Document = fitz.open(input_path)
    try:
        total_pages = len(doc)

        for page_index, page in enumerate(doc):
            page_had_matches = False
            for term in terms:
                rects = page.search_for(term)
                if rects:
                    page_had_matches = True
                    matches_per_term[term] += len(rects)
                    for rect in rects:
                        page.add_redact_annot(rect, fill=fill_color)

            if page_had_matches:
                page.apply_redactions()
                pages_modified += 1

            if progress_callback is not None:
                progress_callback(page_index + 1, total_pages)

        doc.save(str(output_path))
        logger.info("Saved redacted PDF: %s", output_path)
    finally:
        doc.close()

    total_matches = sum(matches_per_term.values())
    terms_not_found = [t for t, count in matches_per_term.items() if count == 0]

    if terms_not_found:
        logger.warning("Terms with no matches: %s", terms_not_found)

    return RedactionResult(
        output_path=output_path,
        total_matches=total_matches,
        matches_per_term=matches_per_term,
        pages_modified=pages_modified,
        pages_total=total_pages,
        terms_not_found=terms_not_found,
    )

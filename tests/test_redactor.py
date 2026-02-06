"""Tests for the PDF redaction engine."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from pdf_redactor.redactor import RedactionResult, parse_terms, redact_pdf


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _create_test_pdf(path: Path, pages: list[str]) -> Path:
    """Create a minimal PDF with the given text on each page."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


# ------------------------------------------------------------------
# parse_terms
# ------------------------------------------------------------------


class TestParseTerms:
    def test_comma_separated(self) -> None:
        assert parse_terms("foo, bar, baz") == ["foo", "bar", "baz"]

    def test_newline_separated(self) -> None:
        assert parse_terms("foo\nbar\nbaz") == ["foo", "bar", "baz"]

    def test_mixed_separators(self) -> None:
        assert parse_terms("foo, bar\nbaz, qux") == ["foo", "bar", "baz", "qux"]

    def test_strips_whitespace(self) -> None:
        assert parse_terms("  foo  ,  bar  ") == ["foo", "bar"]

    def test_removes_empty_terms(self) -> None:
        assert parse_terms("foo,,, ,bar") == ["foo", "bar"]

    def test_deduplicates(self) -> None:
        assert parse_terms("foo, bar, foo, bar") == ["foo", "bar"]

    def test_preserves_order(self) -> None:
        assert parse_terms("cherry, apple, banana") == ["cherry", "apple", "banana"]

    def test_empty_input(self) -> None:
        assert parse_terms("") == []

    def test_whitespace_only(self) -> None:
        assert parse_terms("   \n  \n  ") == []


# ------------------------------------------------------------------
# redact_pdf
# ------------------------------------------------------------------


class TestRedactPdf:
    def test_basic_redaction(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello secret world"])
        result = redact_pdf(pdf_path, ["secret"])

        assert result.total_matches == 1
        assert result.matches_per_term == {"secret": 1}
        assert result.pages_modified == 1
        assert result.pages_total == 1
        assert result.output_path.exists()
        assert result.terms_not_found == []

    def test_multiple_terms(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello secret world confidential data"])
        result = redact_pdf(pdf_path, ["secret", "confidential"])

        assert result.total_matches == 2
        assert result.matches_per_term["secret"] == 1
        assert result.matches_per_term["confidential"] == 1
        assert result.pages_modified == 1

    def test_no_matches(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello world"])
        result = redact_pdf(pdf_path, ["missing"])

        assert result.total_matches == 0
        assert result.pages_modified == 0
        assert result.terms_not_found == ["missing"]

    def test_multiple_pages(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(
            tmp_path / "test.pdf",
            ["Page one secret", "Page two clean", "Page three secret"],
        )
        result = redact_pdf(pdf_path, ["secret"])

        assert result.total_matches == 2
        assert result.pages_modified == 2
        assert result.pages_total == 3

    def test_custom_output_path(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello secret"])
        output = tmp_path / "custom_output.pdf"
        result = redact_pdf(pdf_path, ["secret"], output_path=output)

        assert result.output_path == output
        assert output.exists()

    def test_default_output_path(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "report.pdf", ["data"])
        result = redact_pdf(pdf_path, ["data"])

        assert result.output_path == tmp_path / "report_redacted.pdf"

    def test_progress_callback(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["p1", "p2", "p3"])

        calls: list[tuple[int, int]] = []
        result = redact_pdf(
            pdf_path, ["p1"], progress_callback=lambda cur, tot: calls.append((cur, tot))
        )

        assert calls == [(1, 3), (2, 3), (3, 3)]
        assert result.pages_total == 3

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            redact_pdf(tmp_path / "nonexistent.pdf", ["term"])

    def test_empty_terms_raises(self, tmp_path: Path) -> None:
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello"])
        with pytest.raises(ValueError, match="(?i)at least one"):
            redact_pdf(pdf_path, [])

    def test_redacted_text_is_removed(self, tmp_path: Path) -> None:
        """Verify the redacted text is actually gone from the output PDF."""
        pdf_path = _create_test_pdf(tmp_path / "test.pdf", ["Hello secret world"])
        result = redact_pdf(pdf_path, ["secret"])

        doc = fitz.open(str(result.output_path))
        page_text = doc[0].get_text()
        doc.close()

        assert "secret" not in page_text
        assert "Hello" in page_text

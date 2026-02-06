# PDF Text Redactor

A desktop GUI tool for permanently redacting text from PDF files. Select a PDF, enter the words or phrases to redact, and the tool replaces every match with a black box — irrecoverably removing the underlying text.

## Features

- **Permanent redaction** — matched text is replaced with filled rectangles and removed from the document, not just visually hidden
- **Multi-term input** — enter multiple terms separated by commas or newlines
- **Automatic deduplication** — duplicate terms are ignored
- **"Save As" dialog** — choose where to save the redacted PDF (defaults to `<filename>_redacted.pdf`)
- **Progress bar** — real-time page-by-page progress for large documents
- **Non-blocking UI** — redaction runs on a background thread so the window stays responsive
- **Detailed results** — shows total matches, pages modified, and warns about terms that had no matches
- **Reusable engine** — the redaction logic (`redact_pdf`) can be imported and used independently of the GUI

## Requirements

- Python 3.10+
- [PyMuPDF](https://pymupdf.readthedocs.io/) (installed automatically)
- tkinter (included with most Python installations)

## Installation

```bash
git clone https://github.com/bytePatrol/PDF_Redact.git
cd PDF_Redact

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

## Usage

### GUI

```bash
python -m pdf_redactor
```

1. Click **Browse** to select a PDF
2. Enter words or phrases to redact (one per line, or comma-separated)
3. Click **Redact PDF**
4. Choose where to save the output
5. Review the results summary

### As a library

```python
from pathlib import Path
from pdf_redactor import redact_pdf, parse_terms

terms = parse_terms("secret, confidential, SSN")
result = redact_pdf(Path("document.pdf"), terms)

print(f"Redacted {result.total_matches} matches across {result.pages_modified} pages")
print(f"Saved to: {result.output_path}")
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
src/pdf_redactor/
├── __init__.py      # Public API exports
├── __main__.py      # Entry point (python -m pdf_redactor)
├── redactor.py      # Redaction engine — no GUI dependency
└── gui.py           # Tkinter GUI
tests/
└── test_redactor.py # Unit tests
```

## License

MIT

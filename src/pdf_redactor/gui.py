"""Tkinter GUI for the PDF text redaction tool.

Provides a simple window where the user can select a PDF, enter terms to
redact, and run the redaction with visual progress feedback.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pdf_redactor.redactor import RedactionResult, parse_terms, redact_pdf

logger = logging.getLogger(__name__)

# Layout constants.
_PAD = 10
_ENTRY_WIDTH = 60
_TEXT_HEIGHT = 8


class RedactorApp:
    """Main application window for PDF text redaction.

    Encapsulates all GUI state and delegates redaction work to the
    ``redactor`` module. Long-running redactions are executed on a
    background thread to keep the UI responsive.
    """

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._root.title("PDF Text Redactor")
        self._root.minsize(500, 350)

        self._input_path = tk.StringVar()
        self._is_running = False

        self._build_file_selector()
        self._build_terms_input()
        self._build_controls()
        self._build_status_bar()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_file_selector(self) -> None:
        """Build the input file selection row."""
        frame = tk.Frame(self._root)
        frame.pack(padx=_PAD, pady=_PAD, fill="x")

        tk.Label(frame, text="Input PDF:").pack(side="left")

        entry = tk.Entry(frame, textvariable=self._input_path, width=_ENTRY_WIDTH)
        entry.pack(side="left", padx=5, fill="x", expand=True)

        tk.Button(frame, text="Browse...", command=self._on_browse).pack(side="left")

    def _build_terms_input(self) -> None:
        """Build the redaction terms text area."""
        tk.Label(
            self._root,
            text="Words / letters to redact (one per line or comma-separated):",
        ).pack(anchor="w", padx=_PAD)

        self._terms_text = tk.Text(
            self._root, height=_TEXT_HEIGHT, width=_ENTRY_WIDTH
        )
        self._terms_text.pack(padx=_PAD, pady=(0, _PAD), fill="both", expand=True)

    def _build_controls(self) -> None:
        """Build the action buttons."""
        frame = tk.Frame(self._root)
        frame.pack(pady=(0, 5))

        self._redact_btn = tk.Button(
            frame, text="Redact PDF", command=self._on_redact
        )
        self._redact_btn.pack(side="left", padx=5)

    def _build_status_bar(self) -> None:
        """Build the bottom progress bar and status label."""
        frame = tk.Frame(self._root)
        frame.pack(fill="x", padx=_PAD, pady=(0, _PAD))

        self._status_label = tk.Label(frame, text="Ready", anchor="w")
        self._status_label.pack(fill="x")

        self._progress = ttk.Progressbar(frame, mode="determinate")
        self._progress.pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_browse(self) -> None:
        """Open a file dialog and set the selected PDF path."""
        path = filedialog.askopenfilename(
            title="Select PDF file",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self._input_path.set(path)

    def _on_redact(self) -> None:
        """Validate inputs, pick an output path, then start redaction."""
        if self._is_running:
            return

        input_path_str = self._input_path.get().strip()
        raw_terms = self._terms_text.get("1.0", tk.END).strip()

        # --- Validate inputs ---
        if not input_path_str:
            messagebox.showerror("Error", "Please select an input PDF file.")
            return

        input_path = Path(input_path_str)
        if not input_path.exists():
            messagebox.showerror("Error", f"File not found:\n{input_path}")
            return

        terms = parse_terms(raw_terms)
        if not terms:
            messagebox.showerror(
                "Error", "Please enter at least one word or letter to redact."
            )
            return

        # --- Ask where to save ---
        default_output = input_path.with_name(f"{input_path.stem}_redacted.pdf")
        output_path_str = filedialog.asksaveasfilename(
            title="Save redacted PDF as",
            initialdir=str(input_path.parent),
            initialfile=default_output.name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not output_path_str:
            return  # User cancelled.

        output_path = Path(output_path_str)
        self._start_redaction(input_path, terms, output_path)

    # ------------------------------------------------------------------
    # Background redaction
    # ------------------------------------------------------------------

    def _start_redaction(
        self, input_path: Path, terms: list[str], output_path: Path
    ) -> None:
        """Run redaction on a background thread to keep the UI responsive."""
        self._is_running = True
        self._redact_btn.config(state="disabled")
        self._progress["value"] = 0
        self._set_status("Starting redaction...")

        thread = threading.Thread(
            target=self._run_redaction_thread,
            args=(input_path, terms, output_path),
            daemon=True,
        )
        thread.start()

    def _run_redaction_thread(
        self, input_path: Path, terms: list[str], output_path: Path
    ) -> None:
        """Execute redaction (called from background thread)."""
        try:
            result = redact_pdf(
                input_path=input_path,
                terms=terms,
                output_path=output_path,
                progress_callback=self._on_progress,
            )
            self._root.after(0, self._on_redaction_complete, result)
        except Exception as exc:
            logger.exception("Redaction failed")
            self._root.after(0, self._on_redaction_error, exc)

    def _on_progress(self, current_page: int, total_pages: int) -> None:
        """Update the progress bar (called from background thread)."""
        pct = (current_page / total_pages) * 100
        self._root.after(0, self._update_progress, pct, current_page, total_pages)

    def _update_progress(
        self, pct: float, current_page: int, total_pages: int
    ) -> None:
        """Apply progress update on the main thread."""
        self._progress["value"] = pct
        self._set_status(f"Processing page {current_page} / {total_pages}...")

    # ------------------------------------------------------------------
    # Completion handlers (main thread)
    # ------------------------------------------------------------------

    def _on_redaction_complete(self, result: RedactionResult) -> None:
        """Show results after a successful redaction."""
        self._is_running = False
        self._redact_btn.config(state="normal")
        self._progress["value"] = 100
        self._set_status("Done")

        lines = [
            "Redaction complete.\n",
            f"Total matches redacted: {result.total_matches}",
            f"Pages modified: {result.pages_modified} / {result.pages_total}",
        ]

        if result.terms_not_found:
            lines.append(
                f"\nTerms with no matches: {', '.join(result.terms_not_found)}"
            )

        lines.append(f"\nSaved as:\n{result.output_path}")
        messagebox.showinfo("Done", "\n".join(lines))

    def _on_redaction_error(self, exc: Exception) -> None:
        """Show an error dialog after a failed redaction."""
        self._is_running = False
        self._redact_btn.config(state="normal")
        self._progress["value"] = 0
        self._set_status("Error")
        messagebox.showerror("Error", f"Redaction failed:\n{exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        """Update the status bar label."""
        self._status_label.config(text=text)

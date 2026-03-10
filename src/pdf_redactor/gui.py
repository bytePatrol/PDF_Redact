"""Native macOS GUI for the PDF text redaction tool.

Built with CustomTkinter for a modern, native-feeling interface.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from pdf_redactor.redactor import RedactionResult, parse_terms, redact_pdf

# ── Appearance ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")

_BG      = "#111111"   # window background
_PANEL   = "#1c1c1c"   # card / panel
_INPUT   = "#161616"   # input field bg
_BORDER  = "#2e2e2e"   # subtle borders
_RED     = "#c0392b"   # primary accent (redaction red)
_RED_H   = "#e74c3c"   # hover
_TEXT    = "#e8e4d8"   # primary text  (aged paper white)
_MUTED   = "#6b6b6b"   # secondary text
_DIM     = "#3a3a3a"   # inactive / decorative bars
_GREEN   = "#27ae60"   # success
_WARN    = "#d68910"   # warning


class RedactorApp(ctk.CTk):
    """Main application window."""

    _PAD = 22

    def __init__(self) -> None:
        super().__init__()

        self.title("PDF Redactor")
        self.geometry("520x560")
        self.minsize(480, 500)
        self.configure(fg_color=_BG)

        self._is_running = False
        self._input_path = tk.StringVar()

        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        p = self._PAD

        # Red stripe at very top
        ctk.CTkFrame(self, height=3, fg_color=_RED, corner_radius=0).pack(fill="x")

        # Scrollable main area
        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=_BG,
            scrollbar_fg_color=_BG,
            scrollbar_button_color=_DIM,
            scrollbar_button_hover_color=_MUTED,
        )
        scroll.pack(fill="both", expand=True)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=p, pady=(p, 16))

        ctk.CTkLabel(
            hdr,
            text="PDF Redactor",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=_TEXT,
        ).pack(anchor="w")

        ctk.CTkLabel(
            hdr,
            text="Permanently remove text from PDF documents",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
        ).pack(anchor="w", pady=(3, 10))

        # Decorative redaction bars
        bar_row = ctk.CTkFrame(hdr, fg_color="transparent")
        bar_row.pack(anchor="w", fill="x")
        for w, c in [(64, _RED), (18, _DIM), (96, _DIM), (36, _DIM), (52, _DIM)]:
            ctk.CTkFrame(bar_row, width=w, height=4,
                         fg_color=c, corner_radius=2).pack(side="left", padx=(0, 4))

        # ── 01 · Source Document ───────────────────────────────────────────
        self._section_label(scroll, "01", "Source Document")

        file_card = ctk.CTkFrame(scroll, fg_color=_PANEL, corner_radius=8)
        file_card.pack(fill="x", padx=p, pady=(6, 0))
        file_card.grid_columnconfigure(0, weight=1)

        self._path_entry = ctk.CTkEntry(
            file_card,
            textvariable=self._input_path,
            placeholder_text="No file selected…",
            fg_color=_INPUT,
            border_color=_BORDER,
            border_width=1,
            text_color=_TEXT,
            placeholder_text_color=_MUTED,
            font=ctk.CTkFont(family="Menlo", size=12),
            height=38,
            corner_radius=5,
        )
        self._path_entry.grid(row=0, column=0, padx=(12, 8), pady=12, sticky="ew")

        ctk.CTkButton(
            file_card,
            text="Browse",
            width=86,
            height=38,
            fg_color=_DIM,
            hover_color="#505050",
            text_color=_TEXT,
            font=ctk.CTkFont(size=13),
            corner_radius=5,
            command=self._on_browse,
        ).grid(row=0, column=1, padx=(0, 12), pady=12)

        # ── 02 · Redaction Terms ───────────────────────────────────────────
        self._section_label(scroll, "02", "Redaction Terms", top_pad=18)

        ctk.CTkLabel(
            scroll,
            text="One term per line, or comma-separated",
            font=ctk.CTkFont(size=12),
            text_color=_MUTED,
        ).pack(anchor="w", padx=p, pady=(0, 6))

        self._terms_box = ctk.CTkTextbox(
            scroll,
            height=128,
            fg_color=_PANEL,
            border_color=_BORDER,
            border_width=1,
            text_color=_TEXT,
            font=ctk.CTkFont(family="Menlo", size=13),
            corner_radius=8,
            wrap="word",
            scrollbar_button_color=_DIM,
        )
        self._terms_box.pack(fill="x", padx=p, pady=(0, p))

        # ── 03 · Execute ───────────────────────────────────────────────────
        self._section_label(scroll, "03", "Execute")

        self._redact_btn = ctk.CTkButton(
            scroll,
            text="████  REDACT DOCUMENT  ████",
            height=52,
            fg_color=_RED,
            hover_color=_RED_H,
            text_color=_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=6,
            command=self._on_redact,
        )
        self._redact_btn.pack(fill="x", padx=p, pady=(6, p))

        # ── Progress (hidden until running) ────────────────────────────────
        self._prog_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        self._status_lbl = ctk.CTkLabel(
            self._prog_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=_MUTED,
            anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=p)

        self._progress = ctk.CTkProgressBar(
            self._prog_frame,
            height=6,
            fg_color=_DIM,
            progress_color=_RED,
            corner_radius=3,
        )
        self._progress.pack(fill="x", padx=p, pady=(5, p))
        self._progress.set(0)

        # ── Results (hidden until done) ────────────────────────────────────
        self._results_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        # Divider
        ctk.CTkFrame(
            self._results_frame, height=1, fg_color=_BORDER, corner_radius=0
        ).pack(fill="x", padx=p, pady=(0, 16))

        # Stats row
        stats = ctk.CTkFrame(self._results_frame, fg_color="transparent")
        stats.pack(fill="x", padx=p, pady=(0, 14))
        stats.grid_columnconfigure((0, 1), weight=1)

        self._lbl_matches = self._stat_card(stats, 0, "Matches Removed")
        self._lbl_pages   = self._stat_card(stats, 1, "Pages Modified")

        # Terms breakdown header
        breakdown_hdr = ctk.CTkFrame(
            self._results_frame, fg_color=_PANEL, corner_radius=8
        )
        breakdown_hdr.pack(fill="x", padx=p, pady=(0, 2))
        ctk.CTkLabel(
            breakdown_hdr,
            text="TERM BREAKDOWN",
            font=ctk.CTkFont(size=10),
            text_color=_MUTED,
        ).pack(anchor="w", padx=12, pady=(8, 8))

        # Terms breakdown list
        self._terms_result = ctk.CTkScrollableFrame(
            self._results_frame,
            height=110,
            fg_color=_PANEL,
            corner_radius=8,
            border_color=_BORDER,
            border_width=0,
            scrollbar_button_color=_DIM,
        )
        self._terms_result.pack(fill="x", padx=p, pady=(0, 12))
        self._terms_result.grid_columnconfigure(0, weight=1)

        # Saved path
        self._saved_lbl = ctk.CTkLabel(
            self._results_frame,
            text="",
            font=ctk.CTkFont(family="Menlo", size=11),
            text_color=_MUTED,
            wraplength=460,
            justify="left",
            anchor="w",
        )
        self._saved_lbl.pack(fill="x", padx=p, pady=(0, 10))

        # New redaction button
        ctk.CTkButton(
            self._results_frame,
            text="New Redaction",
            height=38,
            fg_color=_DIM,
            hover_color="#505050",
            text_color=_TEXT,
            font=ctk.CTkFont(size=13),
            corner_radius=6,
            command=self._reset,
        ).pack(fill="x", padx=p, pady=(0, p))

    def _section_label(
        self, parent: ctk.CTkBaseClass, num: str, title: str, top_pad: int = 0
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=self._PAD, pady=(top_pad, 4))

        ctk.CTkLabel(
            row,
            text=f"{num} ——",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=_RED,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            row,
            text=title.upper(),
            font=ctk.CTkFont(size=10),
            text_color=_TEXT,
        ).pack(side="left")

    def _stat_card(
        self, parent: ctk.CTkFrame, col: int, label: str
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color=_PANEL, corner_radius=8)
        pad = (0, 8) if col == 0 else (0, 0)
        card.grid(row=0, column=col, padx=pad, sticky="ew")

        num = ctk.CTkLabel(
            card,
            text="0",
            font=ctk.CTkFont(size=40, weight="bold"),
            text_color=_TEXT,
        )
        num.pack(pady=(14, 0))

        ctk.CTkLabel(
            card,
            text=label.upper(),
            font=ctk.CTkFont(size=9),
            text_color=_MUTED,
        ).pack(pady=(2, 12))

        return num

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self._input_path.set(path)

    def _on_redact(self) -> None:
        if self._is_running:
            return

        input_str = self._input_path.get().strip()
        raw_terms = self._terms_box.get("1.0", tk.END).strip()

        if not input_str:
            self._shake()
            return

        input_path = Path(input_str)
        if not input_path.exists():
            messagebox.showerror("File Not Found", f"Could not find:\n{input_path}", parent=self)
            return

        terms = parse_terms(raw_terms)
        if not terms:
            self._shake()
            return

        default_out = input_path.with_name(f"{input_path.stem}_redacted.pdf")
        out_str = filedialog.asksaveasfilename(
            parent=self,
            title="Save Redacted PDF",
            initialdir=str(input_path.parent),
            initialfile=default_out.name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_str:
            return

        self._start_redaction(input_path, terms, Path(out_str))

    # ── Redaction ──────────────────────────────────────────────────────────────

    def _start_redaction(
        self, input_path: Path, terms: list[str], output_path: Path
    ) -> None:
        self._is_running = True
        self._redact_btn.configure(state="disabled")
        self._results_frame.pack_forget()
        self._progress.set(0)
        self._progress.configure(progress_color=_RED)
        self._status_lbl.configure(text="Starting…", text_color=_MUTED)
        self._prog_frame.pack(fill="x")

        threading.Thread(
            target=self._run_thread,
            args=(input_path, terms, output_path),
            daemon=True,
        ).start()

    def _run_thread(
        self, input_path: Path, terms: list[str], output_path: Path
    ) -> None:
        try:
            result = redact_pdf(
                input_path=input_path,
                terms=terms,
                output_path=output_path,
                progress_callback=lambda c, t: self.after(
                    0, self._update_progress, c, t
                ),
            )
            self.after(0, self._on_complete, result)
        except Exception as exc:
            self.after(0, self._on_error, exc)

    def _update_progress(self, current: int, total: int) -> None:
        self._progress.set(current / total)
        self._status_lbl.configure(
            text=f"Processing page {current} of {total}…"
        )

    def _on_complete(self, result: RedactionResult) -> None:
        self._is_running = False
        self._redact_btn.configure(state="normal")
        self._progress.set(1.0)
        self._progress.configure(progress_color=_GREEN)
        self._status_lbl.configure(text="Complete", text_color=_GREEN)

        # Stats
        self._lbl_matches.configure(text=str(result.total_matches))
        self._lbl_pages.configure(text=str(result.pages_modified))

        # Terms breakdown
        for w in self._terms_result.winfo_children():
            w.destroy()

        for i, (term, count) in enumerate(result.matches_per_term.items()):
            row = ctk.CTkFrame(self._terms_result, fg_color="transparent")
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(0, weight=1)

            display = (term[:36] + "…") if len(term) > 36 else term
            name_bg = _INPUT if count > 0 else "transparent"
            ctk.CTkLabel(
                row,
                text=display,
                font=ctk.CTkFont(family="Menlo", size=12),
                text_color=_TEXT if count > 0 else _MUTED,
                fg_color=name_bg,
                corner_radius=3,
                padx=6,
                pady=1,
            ).grid(row=0, column=0, sticky="w")

            ctk.CTkLabel(
                row,
                text=f"{count} removed" if count > 0 else "not found",
                font=ctk.CTkFont(size=11),
                text_color=_GREEN if count > 0 else _WARN,
            ).grid(row=0, column=1, padx=(10, 4))

        self._saved_lbl.configure(text=f"Saved: {result.output_path}")
        self._results_frame.pack(fill="x")

    def _on_error(self, exc: Exception) -> None:
        self._is_running = False
        self._redact_btn.configure(state="normal")
        self._status_lbl.configure(text=f"Error: {exc}", text_color="#e74c3c")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._progress.set(0)
        self._progress.configure(progress_color=_RED)
        self._prog_frame.pack_forget()
        self._results_frame.pack_forget()
        self._status_lbl.configure(text="", text_color=_MUTED)

    def _shake(self) -> None:
        """Brief shake animation when required input is missing."""
        x, y = self.winfo_x(), self.winfo_y()
        delay = 0
        for dx in [8, -8, 6, -6, 3, -3, 0]:
            delay += 38
            self.after(delay, lambda d=dx: self.geometry(f"+{x + d}+{y}"))

"""Web-based GUI for PDF Redactor.

Launches a local HTTP server and opens the browser to a beautifully
designed interface for uploading and redacting PDF files.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pdf_redactor.redactor import parse_terms, redact_pdf

# ─────────────────────────────────────────────────────────────────────────────
# Job registry
# ─────────────────────────────────────────────────────────────────────────────

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _new_job() -> str:
    jid = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[jid] = {
            "status": "queued",
            "progress": 0,
            "total": 0,
            "result": None,
            "error": None,
            "output_path": None,
        }
    return jid


def _update_job(jid: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kwargs)


def _get_job(jid: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(jid)
        return dict(job) if job else None


# ─────────────────────────────────────────────────────────────────────────────
# Multipart parser
# ─────────────────────────────────────────────────────────────────────────────


def _parse_multipart(
    data: bytes, boundary: str
) -> dict[str, tuple[bytes, str | None]]:
    """Parse multipart/form-data body. Returns {name: (content, filename)}."""
    sep = ("--" + boundary).encode()
    parts: dict[str, tuple[bytes, str | None]] = {}
    for seg in data.split(sep)[1:]:
        if seg.lstrip(b"\r\n").startswith(b"--"):
            break
        header_end = seg.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = seg[:header_end].strip()
        content = seg[header_end + 4 :].rstrip(b"\r\n")
        name: str | None = None
        filename: str | None = None
        for line in headers_raw.split(b"\r\n"):
            line_str = line.decode("utf-8", errors="replace")
            m_name = re.search(r'name="([^"]+)"', line_str)
            m_file = re.search(r'filename="([^"]+)"', line_str)
            if m_name:
                name = m_name.group(1)
            if m_file:
                filename = m_file.group(1)
        if name is not None:
            parts[name] = (content, filename)
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# HTTP handler
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # silence logs
        pass

    # ── helpers ──────────────────────────────────────────────────────────────

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── routing ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_HTML)
        elif parsed.path == "/api/poll":
            qs = parse_qs(parsed.query)
            jid = qs.get("job", [""])[0]
            job = _get_job(jid)
            if job:
                self._send_json(job)
            else:
                self._send_json({"error": "Job not found"}, 404)
        elif parsed.path == "/api/download":
            qs = parse_qs(parsed.query)
            jid = qs.get("job", [""])[0]
            job = _get_job(jid)
            out = job.get("output_path") if job else None
            if out:
                self._serve_file(Path(out))
            else:
                self._send_json({"error": "File not available"}, 404)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/start":
            self._handle_start()
        else:
            self._send_json({"error": "Not found"}, 404)

    # ── file download ─────────────────────────────────────────────────────────

    def _serve_file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{path.name}"'
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── redaction start ───────────────────────────────────────────────────────

    def _handle_start(self) -> None:
        ct = self.headers.get("Content-Type", "")
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl)

        bm = re.search(r"boundary=([^\s;]+)", ct)
        if "multipart/form-data" not in ct or not bm:
            self._send_json({"error": "Expected multipart/form-data"}, 400)
            return

        parts = _parse_multipart(body, bm.group(1))
        file_tuple = parts.get("file")
        terms_raw = (parts.get("terms", (b"", None))[0]).decode("utf-8", errors="replace")

        if not file_tuple or not file_tuple[0]:
            self._send_json({"error": "No PDF file received"}, 400)
            return

        file_data, orig_filename = file_tuple
        if not orig_filename:
            orig_filename = "document.pdf"
        stem = Path(orig_filename).stem
        output_filename = f"{stem}_redacted.pdf"

        terms = parse_terms(terms_raw)
        if not terms:
            self._send_json({"error": "No redaction terms provided"}, 400)
            return

        tmp_dir = tempfile.mkdtemp()
        input_path = Path(tmp_dir) / "input.pdf"
        output_path = Path(tmp_dir) / output_filename
        input_path.write_bytes(file_data)

        jid = _new_job()

        def run() -> None:
            try:
                def cb(current: int, total: int) -> None:
                    _update_job(jid, progress=current, total=total, status="running")

                result = redact_pdf(
                    input_path=input_path,
                    terms=terms,
                    output_path=output_path,
                    progress_callback=cb,
                )
                _update_job(
                    jid,
                    status="done",
                    progress=result.pages_total,
                    total=result.pages_total,
                    output_path=str(output_path),
                    result={
                        "total_matches": result.total_matches,
                        "matches_per_term": result.matches_per_term,
                        "pages_modified": result.pages_modified,
                        "pages_total": result.pages_total,
                        "terms_not_found": result.terms_not_found,
                        "output_filename": output_filename,
                    },
                )
            except Exception as exc:
                _update_job(jid, status="error", error=str(exc))

        threading.Thread(target=run, daemon=True).start()
        self._send_json({"job_id": jid})


# ─────────────────────────────────────────────────────────────────────────────
# HTML / CSS / JS
# ─────────────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PDF Redaction System</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Special+Elite&family=EB+Garamond:ital,wght@0,400;0,600;1,400&family=Courier+Prime:ital,wght@0,400;0,700&display=swap" rel="stylesheet">
  <style>
    :root {
      --paper:       #f0ece0;
      --paper-card:  #f7f4ea;
      --paper-dark:  #e4dfd0;
      --ink:         #19161280;
      --ink-full:    #191612;
      --ink-faded:   #5a5248;
      --red:         #b91c1c;
      --red-muted:   #7f1d1d;
      --border:      #c8bfa8;
      --border-dark: #9a8e78;
      --shadow:      rgba(25,22,18,0.12);
      --font-display:'Special Elite', 'Courier New', cursive;
      --font-body:   'EB Garamond', Georgia, serif;
      --font-mono:   'Courier Prime', 'Courier New', monospace;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    html { scroll-behavior: smooth; }

    body {
      font-family: var(--font-body);
      font-size: 16px;
      color: var(--ink-full);
      min-height: 100vh;
      padding: 0 0 80px;
      background-color: var(--paper);
      background-image:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='0.045'/%3E%3C/svg%3E"),
        repeating-linear-gradient(
          0deg,
          transparent,
          transparent 27px,
          rgba(25,22,18,0.04) 27px,
          rgba(25,22,18,0.04) 28px
        );
    }

    /* ── TOP BANNER ────────────────────────────────────────────── */
    .banner {
      background: var(--ink-full);
      color: var(--paper);
      text-align: center;
      padding: 9px 20px;
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 5px;
      text-transform: uppercase;
      position: relative;
      overflow: hidden;
    }
    .banner::after {
      content: '';
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        90deg,
        transparent 0,
        transparent 3px,
        rgba(255,255,255,0.03) 3px,
        rgba(255,255,255,0.03) 4px
      );
      pointer-events: none;
    }

    /* ── CONTAINER ────────────────────────────────────────────── */
    .wrap {
      max-width: 700px;
      margin: 0 auto;
      padding: 48px 24px 0;
    }

    /* ── HEADER ────────────────────────────────────────────────── */
    .header {
      margin-bottom: 44px;
      opacity: 0;
      transform: translateY(-18px);
      animation: rise 0.7s 0.05s cubic-bezier(0.16,1,0.3,1) forwards;
    }

    .header-row {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 20px;
    }

    .header-left { flex: 1; }

    .bars {
      display: flex;
      gap: 5px;
      margin-bottom: 14px;
    }
    .bar {
      height: 7px;
      background: var(--ink-full);
      border-radius: 1px;
    }

    h1 {
      font-family: var(--font-display);
      font-size: clamp(28px, 6vw, 44px);
      font-weight: normal;
      line-height: 1.05;
      letter-spacing: 0.01em;
      color: var(--ink-full);
      margin-bottom: 10px;
    }

    .subtitle {
      font-family: var(--font-body);
      font-style: italic;
      font-size: 15px;
      color: var(--ink-faded);
      line-height: 1.5;
    }

    .form-meta {
      text-align: right;
      font-family: var(--font-mono);
      font-size: 10.5px;
      color: var(--ink-faded);
      line-height: 1.9;
      white-space: nowrap;
    }
    .form-meta .stamp-class {
      display: inline-block;
      border: 1.5px solid var(--red);
      color: var(--red);
      padding: 2px 7px;
      font-size: 9px;
      letter-spacing: 3px;
      margin-top: 6px;
    }

    .rule {
      margin: 22px 0;
      height: 1px;
      background: var(--border-dark);
      position: relative;
    }
    .rule::after {
      content: '';
      position: absolute;
      left: 0; top: -1px;
      width: 45%;
      height: 3px;
      background: var(--ink-full);
    }

    /* ── SECTIONS ──────────────────────────────────────────────── */
    .section {
      margin-bottom: 30px;
      opacity: 0;
      transform: translateY(14px);
    }
    .section:nth-child(1) { animation: rise 0.65s 0.2s  cubic-bezier(0.16,1,0.3,1) forwards; }
    .section:nth-child(2) { animation: rise 0.65s 0.35s cubic-bezier(0.16,1,0.3,1) forwards; }
    .section:nth-child(3) { animation: rise 0.65s 0.5s  cubic-bezier(0.16,1,0.3,1) forwards; }

    .sec-label {
      display: flex;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 6px;
    }
    .sec-num {
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 700;
      color: var(--red);
      letter-spacing: 2px;
    }
    .sec-title {
      font-family: var(--font-display);
      font-size: 15px;
      letter-spacing: 2.5px;
      text-transform: uppercase;
    }
    .sec-desc {
      font-family: var(--font-body);
      font-style: italic;
      font-size: 13.5px;
      color: var(--ink-faded);
      margin-bottom: 10px;
      padding-left: 36px;
    }

    /* ── DROP ZONE ──────────────────────────────────────────────── */
    .drop-zone {
      border: 2px dashed var(--border-dark);
      background: var(--paper-card);
      padding: 30px 24px 24px;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s, transform 0.2s;
      position: relative;
      user-select: none;
    }
    .drop-zone:hover, .drop-zone.drag-over {
      border-color: var(--ink-full);
      border-style: solid;
      background: var(--paper-dark);
    }
    .drop-zone.has-file {
      border-style: solid;
      border-color: var(--ink-full);
    }
    /* corner fold */
    .drop-zone::after {
      content: '';
      position: absolute;
      bottom: 0; right: 0;
      width: 0; height: 0;
      border-style: solid;
      border-width: 0 0 18px 18px;
      border-color: transparent transparent var(--paper-dark) transparent;
      transition: border-width 0.2s;
    }
    .drop-zone:hover::after, .drop-zone.has-file::after {
      border-width: 0 0 24px 24px;
      border-color: transparent transparent var(--border) transparent;
    }

    #file-input { display: none; }

    .drop-icon {
      font-size: 36px;
      line-height: 1;
      display: block;
      margin-bottom: 12px;
      transition: transform 0.3s;
    }
    .drop-zone:hover .drop-icon { transform: scale(1.08) rotate(-3deg); }

    .drop-primary {
      font-family: var(--font-display);
      font-size: 16px;
      letter-spacing: 1px;
      margin-bottom: 5px;
    }
    .drop-secondary {
      font-family: var(--font-body);
      font-style: italic;
      font-size: 13px;
      color: var(--ink-faded);
    }

    .file-info {
      display: none;
      margin-top: 14px;
      font-family: var(--font-mono);
      font-size: 12px;
      background: var(--paper-dark);
      border-left: 3px solid var(--ink-full);
      padding: 7px 12px;
      text-align: left;
      color: var(--ink-full);
    }
    .file-info.show { display: block; }

    /* ── TERMS ──────────────────────────────────────────────────── */
    .terms-field {
      display: flex;
      border: 2px solid var(--border-dark);
      background: var(--paper-card);
      transition: border-color 0.2s;
      position: relative;
    }
    .terms-field:focus-within { border-color: var(--ink-full); }

    .line-nums {
      width: 36px;
      padding: 11px 6px;
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 24.5px;
      color: var(--border-dark);
      text-align: right;
      user-select: none;
      border-right: 1px solid var(--border);
      background: var(--paper-dark);
      white-space: pre;
      min-height: 147px;
    }

    textarea {
      flex: 1;
      min-height: 147px;
      padding: 11px 13px;
      border: none;
      outline: none;
      resize: vertical;
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 24.5px;
      color: var(--ink-full);
      background: transparent;
    }
    textarea::placeholder { color: var(--border-dark); }

    /* ── BUTTON ────────────────────────────────────────────────── */
    .btn-redact {
      display: block;
      width: 100%;
      padding: 19px 32px;
      background: var(--ink-full);
      color: var(--paper);
      border: none;
      cursor: pointer;
      font-family: var(--font-display);
      font-size: 19px;
      letter-spacing: 7px;
      text-transform: uppercase;
      position: relative;
      overflow: hidden;
      transition: background 0.15s, letter-spacing 0.2s;
      opacity: 0;
      animation: rise 0.65s 0.65s cubic-bezier(0.16,1,0.3,1) forwards;
    }
    .btn-redact:hover:not(:disabled) {
      background: #2c2820;
      letter-spacing: 9px;
    }
    .btn-redact:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .btn-redact .shimmer {
      position: absolute;
      inset: 0;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.14) 50%, transparent 100%);
      transform: translateX(-100%);
      transition: none;
    }
    .btn-redact:hover:not(:disabled) .shimmer {
      animation: shimmer 0.7s ease forwards;
    }

    /* ── SCAN LINE (processing) ─────────────────────────────────── */
    .scan-wrap {
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 50;
      display: none;
      overflow: hidden;
    }
    .scan-wrap.active { display: block; }
    .scan-line {
      position: absolute;
      left: 0; right: 0;
      height: 2px;
      background: linear-gradient(90deg, transparent 5%, var(--red) 30%, rgba(185,28,28,0.8) 50%, var(--red) 70%, transparent 95%);
      animation: scan 1.8s linear infinite;
      opacity: 0.55;
    }

    /* ── STATUS ────────────────────────────────────────────────── */
    .status-wrap {
      margin-top: 28px;
      opacity: 0;
      transition: opacity 0.4s;
    }
    .status-wrap.show { opacity: 1; }

    .status-label {
      display: flex;
      justify-content: space-between;
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: var(--ink-faded);
      margin-bottom: 8px;
    }

    .progress-track {
      height: 22px;
      background: var(--paper-dark);
      border: 1px solid var(--border-dark);
      position: relative;
      overflow: hidden;
    }
    .progress-fill {
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 0%;
      background: var(--ink-full);
      transition: width 0.35s ease;
    }
    .progress-fill::after {
      content: '';
      position: absolute;
      right: 0; top: 0; bottom: 0;
      width: 30px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15));
    }
    .progress-pages {
      position: absolute;
      right: 8px;
      top: 50%;
      transform: translateY(-50%);
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--paper);
      mix-blend-mode: difference;
    }

    /* ── ERROR ─────────────────────────────────────────────────── */
    .error-box {
      display: none;
      margin-top: 24px;
      border: 2px solid var(--red);
      padding: 14px 16px;
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--red);
      letter-spacing: 0.5px;
      background: rgba(185,28,28,0.04);
    }
    .error-box.show { display: block; }

    /* ── RESULTS ───────────────────────────────────────────────── */
    .results {
      display: none;
      margin-top: 36px;
    }
    .results.show { display: block; }

    .stamp-wrap {
      text-align: center;
      margin-bottom: 28px;
    }
    .stamp {
      display: inline-block;
      border: 4px double var(--red);
      color: var(--red);
      font-family: var(--font-display);
      font-size: 30px;
      letter-spacing: 7px;
      padding: 8px 26px;
      transform: rotate(-2.5deg);
      opacity: 0;
      animation: stamp-in 0.55s 0.1s cubic-bezier(0.34,1.56,0.64,1) forwards;
      position: relative;
    }
    .stamp::before {
      content: '';
      position: absolute;
      inset: 3px;
      border: 1px solid var(--red);
      opacity: 0.4;
    }

    .stats-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-bottom: 22px;
    }
    .stat-card {
      background: var(--paper-card);
      border: 1px solid var(--border-dark);
      padding: 18px 16px;
      opacity: 0;
      animation: rise 0.45s cubic-bezier(0.16,1,0.3,1) forwards;
    }
    .stat-card:nth-child(1) { animation-delay: 0.15s; }
    .stat-card:nth-child(2) { animation-delay: 0.28s; }
    .stat-num {
      font-family: var(--font-display);
      font-size: 46px;
      line-height: 1;
      margin-bottom: 5px;
    }
    .stat-label {
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: var(--ink-faded);
    }

    .breakdown {
      border: 1px solid var(--border-dark);
      overflow: hidden;
      margin-bottom: 20px;
    }
    .breakdown-head {
      background: var(--ink-full);
      color: var(--paper);
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 3px;
      text-transform: uppercase;
      padding: 8px 14px;
    }
    .term-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 14px;
      border-bottom: 1px solid var(--border);
      font-family: var(--font-mono);
      font-size: 12.5px;
      opacity: 0;
      animation: slide-r 0.3s ease forwards;
    }
    .term-row:last-child { border-bottom: none; }
    .term-name-tag {
      background: var(--ink-full);
      color: var(--paper);
      padding: 2px 9px;
      font-size: 12px;
      max-width: 60%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .term-row.not-found .term-name-tag {
      background: var(--border-dark);
      color: var(--ink-faded);
    }
    .term-count { color: var(--ink-faded); font-size: 11px; }

    .warning-box {
      display: none;
      font-family: var(--font-mono);
      font-size: 11.5px;
      color: #854d0e;
      background: #fefce8;
      border: 1px solid #ca8a04;
      padding: 10px 14px;
      margin-bottom: 18px;
    }
    .warning-box.show { display: block; }

    .dl-btn {
      display: block;
      width: 100%;
      padding: 15px 24px;
      background: var(--paper-card);
      border: 2px solid var(--ink-full);
      color: var(--ink-full);
      font-family: var(--font-display);
      font-size: 17px;
      letter-spacing: 4px;
      text-transform: uppercase;
      text-align: center;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.18s, color 0.18s, letter-spacing 0.2s;
    }
    .dl-btn:hover {
      background: var(--ink-full);
      color: var(--paper);
      letter-spacing: 5px;
    }

    /* ── FOOTER ────────────────────────────────────────────────── */
    .footer {
      margin-top: 56px;
      padding: 16px 0;
      border-top: 1px solid var(--border);
      text-align: center;
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 2px;
      color: var(--border-dark);
      text-transform: uppercase;
    }

    /* ── ANIMATIONS ────────────────────────────────────────────── */
    @keyframes rise {
      to { opacity: 1; transform: none; }
    }
    @keyframes shimmer {
      to { transform: translateX(220%); }
    }
    @keyframes stamp-in {
      0%   { opacity: 0; transform: rotate(-2.5deg) scale(2.4); }
      100% { opacity: 1; transform: rotate(-2.5deg) scale(1); }
    }
    @keyframes slide-r {
      from { opacity: 0; transform: translateX(-12px); }
      to   { opacity: 1; transform: none; }
    }
    @keyframes scan {
      from { top: -2px; }
      to   { top: 100%; }
    }

    /* ── RESPONSIVE ────────────────────────────────────────────── */
    @media (max-width: 520px) {
      .stats-grid { grid-template-columns: 1fr; }
      .header-row { flex-direction: column; }
      .form-meta { text-align: left; }
    }
  </style>
</head>
<body>

  <div class="banner">Classified &mdash; Authorized Personnel Only &mdash; PDF Redaction System</div>

  <div class="scan-wrap" id="scan-wrap">
    <div class="scan-line"></div>
  </div>

  <div class="wrap">

    <!-- ── Header ── -->
    <header class="header">
      <div class="header-row">
        <div class="header-left">
          <div class="bars" aria-hidden="true">
            <div class="bar" style="flex:3"></div>
            <div class="bar" style="flex:1"></div>
            <div class="bar" style="flex:4"></div>
            <div class="bar" style="flex:1.5"></div>
            <div class="bar" style="flex:2"></div>
          </div>
          <h1>Redaction<br>Request Form</h1>
          <p class="subtitle">Permanent, irrecoverable text removal<br>from PDF document content streams</p>
        </div>
        <div class="form-meta">
          FORM NO.&nbsp;PDR&#8209;7731<br>
          <span id="datestamp">——/——/——</span><br>
          REV.&nbsp;2024&#8209;C<br>
          <span class="stamp-class">CUI // SP-RDCT</span>
        </div>
      </div>
      <div class="rule"></div>
    </header>

    <!-- ── Sections ── -->
    <div id="sections">

      <!-- 01: Document -->
      <div class="section">
        <div class="sec-label">
          <span class="sec-num">01 ——</span>
          <span class="sec-title">Source Document</span>
        </div>
        <p class="sec-desc">Select the PDF file to be processed for permanent redaction</p>

        <div class="drop-zone" id="drop-zone" role="button" tabindex="0" aria-label="Select PDF file">
          <input type="file" id="file-input" accept=".pdf">
          <span class="drop-icon" aria-hidden="true">&#9646;</span>
          <div class="drop-primary">Drop PDF here &mdash; or click to browse</div>
          <div class="drop-secondary">Accepts .pdf files only &middot; Max recommended: 200 MB</div>
          <div class="file-info" id="file-info"></div>
        </div>
      </div>

      <!-- 02: Terms -->
      <div class="section">
        <div class="sec-label">
          <span class="sec-num">02 ——</span>
          <span class="sec-title">Redaction Terms</span>
        </div>
        <p class="sec-desc">One term per line, or comma-separated &mdash; duplicates are removed automatically</p>

        <div class="terms-field">
          <div class="line-nums" id="line-nums" aria-hidden="true">1</div>
          <textarea
            id="terms"
            spellcheck="false"
            autocomplete="off"
            placeholder="John Doe&#10;123-45-6789&#10;confidential@example.com"
            aria-label="Redaction terms"
          ></textarea>
        </div>
      </div>

      <!-- 03: Execute -->
      <div class="section">
        <div class="sec-label">
          <span class="sec-num">03 ——</span>
          <span class="sec-title">Execute Redaction</span>
        </div>

        <button class="btn-redact" id="redact-btn" disabled aria-label="Start redaction">
          <span class="shimmer" aria-hidden="true"></span>
          &#9608;&#9608;&#9608;&#9608; REDACT DOCUMENT &#9608;&#9608;&#9608;&#9608;
        </button>
      </div>

    </div><!-- /sections -->

    <!-- ── Status ── -->
    <div class="status-wrap" id="status-wrap">
      <div class="status-label">
        <span>Processing Document</span>
        <span id="status-text">Initializing&hellip;</span>
      </div>
      <div class="progress-track" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100" id="prog-bar">
        <div class="progress-fill" id="prog-fill"></div>
        <span class="progress-pages" id="prog-pages"></span>
      </div>
    </div>

    <!-- ── Error ── -->
    <div class="error-box" id="error-box" role="alert"></div>

    <!-- ── Results ── -->
    <div class="results" id="results">
      <div class="rule"></div>
      <div class="stamp-wrap">
        <div class="stamp">REDACTED</div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-num" id="stat-matches">0</div>
          <div class="stat-label">Matches Removed</div>
        </div>
        <div class="stat-card">
          <div class="stat-num" id="stat-pages">0</div>
          <div class="stat-label">Pages Modified</div>
        </div>
      </div>

      <div class="breakdown">
        <div class="breakdown-head">Term&#8209;by&#8209;Term Breakdown</div>
        <div id="terms-list"></div>
      </div>

      <div class="warning-box" id="warnings"></div>

      <a class="dl-btn" id="dl-btn" href="#" download="redacted.pdf">
        &#9660;&nbsp; Download Redacted Document
      </a>
    </div>

    <footer class="footer">
      PDF Redaction System &mdash; Text Data Permanently Stripped from Content Stream<br>
      Redacted regions are irrecoverable &middot; Verify output before distribution
    </footer>
  </div><!-- /wrap -->

<script>
(function () {
  'use strict';

  // ── Datestamp ───────────────────────────────────────────────────
  const d = new Date();
  document.getElementById('datestamp').textContent =
    String(d.getFullYear()) + '/' +
    String(d.getMonth() + 1).padStart(2, '0') + '/' +
    String(d.getDate()).padStart(2, '0');

  // ── Elements ────────────────────────────────────────────────────
  const dropZone   = document.getElementById('drop-zone');
  const fileInput  = document.getElementById('file-input');
  const fileInfo   = document.getElementById('file-info');
  const termsEl    = document.getElementById('terms');
  const lineNums   = document.getElementById('line-nums');
  const redactBtn  = document.getElementById('redact-btn');
  const scanWrap   = document.getElementById('scan-wrap');
  const statusWrap = document.getElementById('status-wrap');
  const statusText = document.getElementById('status-text');
  const progFill   = document.getElementById('prog-fill');
  const progPages  = document.getElementById('prog-pages');
  const progBar    = document.getElementById('prog-bar');
  const errorBox   = document.getElementById('error-box');
  const results    = document.getElementById('results');
  const dlBtn      = document.getElementById('dl-btn');

  let selectedFile  = null;
  let pollTimer     = null;

  // ── Line numbers ────────────────────────────────────────────────
  function updateLineNums() {
    const count = Math.max(termsEl.value.split('\\n').length, 5);
    lineNums.textContent = Array.from({length: count}, (_, i) => i + 1).join('\\n');
  }
  termsEl.addEventListener('input', updateLineNums);
  updateLineNums();

  // ── File handling ───────────────────────────────────────────────
  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') fileInput.click(); });

  fileInput.addEventListener('change', e => {
    const f = e.target.files && e.target.files[0];
    if (f) acceptFile(f);
  });

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f && f.name.toLowerCase().endsWith('.pdf')) acceptFile(f);
  });

  function acceptFile(f) {
    selectedFile = f;
    dropZone.classList.add('has-file');
    const kb = (f.size / 1024).toFixed(1);
    fileInfo.textContent = '\\u25b6 ' + f.name + '  (' + kb + '\\u202fKB)';
    fileInfo.classList.add('show');
    checkReady();
  }

  termsEl.addEventListener('input', checkReady);

  function checkReady() {
    redactBtn.disabled = !(selectedFile && termsEl.value.trim());
  }

  // ── Redact ──────────────────────────────────────────────────────
  redactBtn.addEventListener('click', async () => {
    if (!selectedFile || !termsEl.value.trim()) return;

    // reset state
    redactBtn.disabled = true;
    errorBox.classList.remove('show');
    results.classList.remove('show');
    statusWrap.classList.add('show');
    setProgress(0, '', 'Uploading document\u2026');
    scanWrap.classList.add('active');

    const fd = new FormData();
    fd.append('file', selectedFile, selectedFile.name);
    fd.append('terms', termsEl.value);

    try {
      const res = await fetch('/api/start', { method: 'POST', body: fd });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt);
      }
      const data = await res.json();
      poll(data.job_id);
    } catch (err) {
      stopScan();
      showError('Upload failed: ' + err.message);
    }
  });

  // ── Polling ─────────────────────────────────────────────────────
  function poll(jobId) {
    statusText.textContent = 'Processing\u2026';
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/poll?job=' + jobId);
        const job = await res.json();

        if (job.status === 'running' && job.total > 0) {
          const pct = (job.progress / job.total) * 100;
          setProgress(pct, job.progress + '/' + job.total,
            'Page ' + job.progress + ' of ' + job.total);
        }

        if (job.status === 'done') {
          clearInterval(pollTimer);
          setProgress(100, job.total + '/' + job.total, 'Complete');
          stopScan();
          setTimeout(() => showResults(job.result, jobId), 700);
        }

        if (job.status === 'error') {
          clearInterval(pollTimer);
          stopScan();
          showError(job.error || 'Unknown error occurred');
        }
      } catch (err) {
        clearInterval(pollTimer);
        stopScan();
        showError('Connection error: ' + err.message);
      }
    }, 280);
  }

  // ── Helpers ─────────────────────────────────────────────────────
  function setProgress(pct, pages, label) {
    progFill.style.width = pct + '%';
    progPages.textContent = pages;
    statusText.textContent = label;
    progBar.setAttribute('aria-valuenow', Math.round(pct));
  }

  function stopScan() {
    scanWrap.classList.remove('active');
  }

  function countUp(el, target, ms) {
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min((now - start) / ms, 1);
      const ease = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(ease * target);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function showResults(r, jobId) {
    redactBtn.disabled = false;
    results.classList.add('show');

    countUp(document.getElementById('stat-matches'), r.total_matches, 900);
    countUp(document.getElementById('stat-pages'),   r.pages_modified, 900);

    const list = document.getElementById('terms-list');
    list.innerHTML = '';
    const entries = Object.entries(r.matches_per_term);
    entries.forEach(([term, count], i) => {
      const row = document.createElement('div');
      row.className = 'term-row' + (count === 0 ? ' not-found' : '');
      row.style.animationDelay = (i * 55) + 'ms';
      const label = term.length > 32 ? term.slice(0, 32) + '\\u2026' : term;
      const countStr = count === 0
        ? 'NOT FOUND'
        : count + ' occurrence' + (count !== 1 ? 's' : '') + ' removed';
      row.innerHTML =
        '<span class="term-name-tag">' + escHtml(label) + '</span>' +
        '<span class="term-count">' + escHtml(countStr) + '</span>';
      list.appendChild(row);
    });

    if (r.terms_not_found && r.terms_not_found.length > 0) {
      const wb = document.getElementById('warnings');
      wb.textContent = '\\u26a0\\ufe0f  Terms with no matches: ' + r.terms_not_found.join(', ');
      wb.classList.add('show');
    }

    dlBtn.href = '/api/download?job=' + jobId;
    dlBtn.setAttribute('download', r.output_filename || 'redacted.pdf');

    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function showError(msg) {
    statusWrap.classList.remove('show');
    errorBox.textContent = '\\u2715  OPERATION FAILED: ' + msg;
    errorBox.classList.add('show');
    redactBtn.disabled = false;
    errorBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
})();
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Server entry point
# ─────────────────────────────────────────────────────────────────────────────


def run(port: int = 0) -> None:
    """Start the local web server and open the browser.

    Binds to an OS-assigned port (port=0) so it never conflicts with
    other services. Blocks until the process is interrupted.
    """
    server = HTTPServer(("127.0.0.1", port), _Handler)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{actual_port}"
    print(f"PDF Redaction System running at {url}")
    webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()

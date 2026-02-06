"""Entry point for ``python -m pdf_redactor``.

Launches the tkinter GUI application.
"""

import logging
import tkinter as tk

from pdf_redactor.gui import RedactorApp


def main() -> None:
    """Create the root window and start the application event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    root = tk.Tk()
    RedactorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

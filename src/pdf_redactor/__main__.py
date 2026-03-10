"""Entry point for ``python -m pdf_redactor``.

Launches the native GUI application.
"""

import logging


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from pdf_redactor.gui import RedactorApp
    app = RedactorApp()
    app.mainloop()


if __name__ == "__main__":
    main()

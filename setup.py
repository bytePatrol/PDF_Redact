"""py2app build configuration for PDF Redactor."""
from setuptools import setup, dist
# Prevent pyproject.toml dependencies from leaking into py2app as install_requires
dist.Distribution.install_requires = []

APP = ["app_main.py"]

OPTIONS = {
    "iconfile": "PDF Redactor.app/Contents/Resources/AppIcon.icns",
    "plist": {
        "CFBundleName": "PDF Redactor",
        "CFBundleDisplayName": "PDF Redactor",
        "CFBundleIdentifier": "com.pdf-redactor.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "PDF Redactor — Permanent text removal from PDF documents",
    },
    "packages": [
        "pdf_redactor",
        "customtkinter",
        "fitz",
        "tkinter",
    ],
    "includes": [
        "darkdetect",
        "packaging",
    ],
}

setup(
    name="PDF Redactor",
    app=APP,
    options={"py2app": OPTIONS},

)

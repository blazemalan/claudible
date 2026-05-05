"""py2app build config for Claudible.app

Build:
    cd app
    python -m pip install py2app
    python setup.py py2app -A     # alias mode (fast, dev)
    python setup.py py2app        # standalone (slower, distributable)
"""
from setuptools import setup

APP = ["main.py"]
OPTIONS = {
    "iconfile": "icon.icns",
    "plist": {
        "CFBundleName": "Claudible",
        "CFBundleDisplayName": "Claudible",
        "CFBundleIdentifier": "io.github.bmalan.claudible",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "MIT",
    },
    "packages": ["rumps"],
    "resources": ["menubarTemplate.png"],
}

setup(
    app=APP,
    name="Claudible",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

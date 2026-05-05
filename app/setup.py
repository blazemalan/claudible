"""py2app build config for claude-tts.app

Build:
    cd app
    python -m pip install py2app
    python setup.py py2app -A     # alias mode (fast, dev)
    python setup.py py2app        # standalone (slower, distributable)
"""
from setuptools import setup

APP = ["main.py"]
OPTIONS = {
    "iconfile": None,
    "plist": {
        "CFBundleName": "claude-tts",
        "CFBundleDisplayName": "claude-tts",
        "CFBundleIdentifier": "io.github.bmalan.claude-tts",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "MIT",
    },
    "packages": ["rumps"],
}

setup(
    app=APP,
    name="claude-tts",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

import sys
from pathlib import Path

import pytest

# Make `import claudible_core` work for all tests, mimicking main.py's runtime
# environment.
app_dir = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(app_dir))

import claudible_core


@pytest.fixture(autouse=True)
def _isolate_log(tmp_path, monkeypatch):
    """Keep test logging out of the real /tmp/claudible.log. Tests that feed
    corrupt input (e.g. bad voices.json) log parse errors, and those lines
    otherwise land in the running app's log, looking like live app failures."""
    monkeypatch.setattr(claudible_core, "LOG_FILE", tmp_path / "claudible-test.log")

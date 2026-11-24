"""
Makes the app's modules importable from the tests, both in a checkout of the
repo (code in app/) and in the image's test stage (code next to tests/).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP_DIR = ROOT / "app" if (ROOT / "app").is_dir() else ROOT

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The cycle logs every step; keep the test output to the test results.
import logging  # noqa: E402

logging.disable(logging.CRITICAL)

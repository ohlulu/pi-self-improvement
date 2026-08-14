"""Test package.

Puts `src/` on `sys.path` so the suite runs from a checkout without an install:
`python3 -m unittest discover -s tests` and `python3 -m unittest tests.test_parse`
both work from the repository root.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

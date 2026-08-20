"""Development-only Archimedes tests; excluded from the competition agent bundle."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agents" / "archimedes"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

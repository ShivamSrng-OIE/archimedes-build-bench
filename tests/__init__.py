"""Test package for the Archimedes repair agent.

These tests are for development only and are not part of the submission ZIP. Importing
the package puts ``agents/archimedes`` at the front of ``sys.path`` so that test modules
can import the agent's code as ``src``.
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agents" / "archimedes"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

"""The evidence ledger and the rules that turn evidence into claim confidence.

``ledger`` stores evidence items and claims; ``confidence`` fuses evidence into
a confidence and status and decides whether a claim may justify a patch.
"""

from .ledger import EvidenceLedger

__all__ = ["EvidenceLedger"]

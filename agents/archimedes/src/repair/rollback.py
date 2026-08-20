"""Public reversible-edit history API independent of Git."""

from .executor import EditTransaction, rollback_transaction

__all__ = ["EditTransaction", "rollback_transaction"]

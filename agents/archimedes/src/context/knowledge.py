"""Optional external-knowledge boundary; the competition-safe default is offline."""

from __future__ import annotations

from typing import Protocol

from ..schemas import EvidenceItem


class KnowledgeProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def lookup(self, query: str) -> list[EvidenceItem]: ...


class UnavailableKnowledgeProvider:
    available = False

    def lookup(self, query: str) -> list[EvidenceItem]:
        return []

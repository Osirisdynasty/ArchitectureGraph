from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    name: str
    qualified_name: str = ""
    file_path: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    confidence: float = 1.0
    provenance: str = "ast"
    evidence: str = ""
    commit: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]: return asdict(self)

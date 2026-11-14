"""External data & annotation ingestion (strictly offline, no network calls)."""
from __future__ import annotations

from autotrade_v3.external.annotations import AnnotationQueryResult, AnnotationRecord, AnnotationStore

__all__ = [
    "AnnotationQueryResult",
    "AnnotationRecord",
    "AnnotationStore",
]

"""External data & annotation ingestion (strictly offline, no network calls)."""
from __future__ import annotations

from autotrade_v3.external.annotations import AnnotationQueryResult, AnnotationRecord, AnnotationStore
from autotrade_v3.external.ledger_import import OPTIONAL_EXTERNAL_COLS, REQUIRED_EXTERNAL_COLS, import_external_ledger

__all__ = [
    "AnnotationQueryResult",
    "AnnotationRecord",
    "AnnotationStore",
    "OPTIONAL_EXTERNAL_COLS",
    "REQUIRED_EXTERNAL_COLS",
    "import_external_ledger",
]

"""Base filter protocol, causal FilterContext, and filter pipeline runner."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from autotrade_v3.l2_lab8.external.annotations import AnnotationQueryResult, AnnotationStore
from autotrade_v3.l2_lab8.runstore.hashing import canonical_json, sha256_text
from autotrade_v3.l2_lab8.sim.types import SignalDecision


@dataclass(frozen=True)
class FilterDecision:
    action: Literal["KEEP", "DROP"]
    reason: str = ""
    label: str = ""


class FilterContext:
    """Causal context passed to filters.

    Filters have NO direct DB/filesystem access to annotations; they can ONLY
    call :meth:`get_annotations`, which strictly enforces ``as_of_ms <= signal.signal_close_ms``.
    """

    def __init__(self, annotation_store: AnnotationStore | None = None) -> None:
        self._annotation_store = annotation_store
        self.dropped_future_annotations: int = 0
        self.used_unverified_annotations: bool = False

    def get_annotations(
        self,
        symbol: str,
        signal_close_ms: int,
        *,
        source: str | None = None,
    ) -> AnnotationQueryResult:
        if self._annotation_store is None:
            return AnnotationQueryResult(records=[], dropped_future_count=0, has_unverified=False)
        res = self._annotation_store.query_as_of(symbol, int(signal_close_ms), source=source)
        self.dropped_future_annotations += res.dropped_future_count
        if res.has_unverified:
            self.used_unverified_annotations = True
        return res


class SignalFilter(Protocol):
    name: str

    def config_dict(self) -> dict[str, Any]: ...

    def config_hash(self) -> str: ...

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision: ...


@dataclass
class FilterRunResult:
    kept_signals: list[Any]
    filtered_decisions: list[SignalDecision]
    labels: dict[str, list[str]] = field(default_factory=dict)
    dropped_future_annotations: int = 0
    used_unverified_annotations: bool = False


def filters_manifest_spec(filters: list[SignalFilter]) -> list[dict[str, str]]:
    return [{"name": str(f.name), "config_hash": str(f.config_hash())} for f in filters]


def _resolve_sig_id(sig: Any) -> str:
    sid = getattr(sig, "signal_id", "")
    if sid:
        return str(sid)
    return f"SIG_{str(sig.symbol).replace('-', '')}_{sig.combo_name}_{sig.timeframe}_{sig.side}_{sig.signal_close_ms}"


def apply_filters(
    signals: list[Any],
    filters: list[SignalFilter],
    ctx: FilterContext,
) -> FilterRunResult:
    """Apply *filters* in order to *signals* after cache and before simulation."""
    if not filters:
        return FilterRunResult(kept_signals=list(signals), filtered_decisions=[])

    kept: list[Any] = []
    filtered_decisions: list[SignalDecision] = []
    labels: dict[str, list[str]] = {}

    for sig in signals:
        sid = _resolve_sig_id(sig)
        dropped_by: str | None = None
        for flt in filters:
            dec = flt.evaluate(sig, ctx)
            if dec.label:
                labels.setdefault(sid, []).append(dec.label)
            if dec.action == "DROP":
                dropped_by = dec.reason or flt.name
                break
        if dropped_by is not None:
            filtered_decisions.append(
                SignalDecision(
                    signal_id=sid,
                    symbol=str(sig.symbol),
                    combo_id=int(sig.combo_id),
                    combo_name=str(sig.combo_name),
                    timeframe=str(sig.timeframe),
                    side=str(sig.side),
                    signal_close_ms=int(sig.signal_close_ms),
                    signal_close_dt=str(sig.signal_close_dt),
                    status="FILTERED",
                    reason=str(dropped_by),
                )
            )
        else:
            kept.append(sig)

    return FilterRunResult(
        kept_signals=kept,
        filtered_decisions=filtered_decisions,
        labels=labels,
        dropped_future_annotations=ctx.dropped_future_annotations,
        used_unverified_annotations=ctx.used_unverified_annotations,
    )


def hash_filter_config(cfg: dict[str, Any]) -> str:
    return sha256_text(canonical_json(cfg))

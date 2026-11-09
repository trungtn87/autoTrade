"""Read-only BingX execution feedback through the Dashboard's existing projection.

The Dashboard already handles exact-order fills and merged-position allocation.
Never reimplement its outcome/ROI formula in the AI service. No BingX API calls,
no trade placement, and no conjectural candle-based fills.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)

CLOSED = {"CLOSED", "ESTIMATED_CLOSED"}
ALLOWED = CLOSED | {"CLOSED_PENDING", "UNRESOLVED"}


def _source_quality(row: dict) -> str:
    status = row.get("status")
    source = row.get("source")
    if status == "ESTIMATED_CLOSED":
        return "MERGED_POSITION_ESTIMATE"
    if status == "CLOSED" and source == "bingx_db_verified":
        return "BINGX_VERIFIED_EXIT"
    if status == "CLOSED" and source == "bingx_db_roi_estimate":
        return "BINGX_VERIFIED_EXIT_ESTIMATED_MONEY"
    if status == "CLOSED" and source == "existing_history":
        return "MANUAL_HISTORICAL_RECORD"
    if status == "CLOSED":
        return "OTHER_RECONCILED_SOURCE"
    return "NOT_CLOSED"


def normalize_feedback(row: dict | None, entry_order_id: str | None) -> dict:
    """Do not call a signal without signal_links NOT_EXECUTED."""
    if not entry_order_id:
        return {
            "status": "NO_CONFIRMED_ENTRY_LINK", "entry_order_id": None,
            "outcome": None, "pnl": None, "roi": None,
            "quality": "NO_EXCHANGE_ENTRY_PROOF", "source": None,
        }
    if row is None:
        return {
            "status": "ENTRY_LINKED_RESULT_UNAVAILABLE",
            "entry_order_id": entry_order_id,
            "outcome": None, "pnl": None, "roi": None,
            "quality": "INSUFFICIENT_EXIT_EVIDENCE", "source": None,
        }
    status = row.get("status")
    if status not in ALLOWED:
        status = "UNRESOLVED"
    closed = status in CLOSED
    return {
        "status": status,
        "entry_order_id": entry_order_id,
        "closed_ms": row.get("closed_ms") if closed else None,
        "outcome": row.get("outcome") if closed else None,
        "pnl": row.get("pnl") if closed else None,
        "roi": row.get("roi") if closed else None,
        "quality": _source_quality(row) if closed else "INSUFFICIENT_EXIT_EVIDENCE",
        "source": row.get("source"),
        "reason": row.get("reason"),
        "shared_position": bool(row.get("shared_position")),
    }


def load_bingx_feedback(conn, reviews: list[dict]) -> dict[str, dict]:
    """Match exact signal_id -> linked ENTRY -> Dashboard result (same DB).

    Never match by timestamp/Combo alone. Multiple entries for one signal remain
    unresolved rather than arbitrarily picking one.
    """
    event_ids = list(dict.fromkeys(r.get("event_id") for r in reviews if r.get("event_id")))
    if not event_ids:
        return {}
    links = conn.execute("""
        SELECT signal_id,entry_order_id,symbol
          FROM autotrade_analytics.signal_links
         WHERE signal_id=ANY(%s::text[])
    """, (event_ids,)).fetchall()
    grouped = {}
    for link in links:
        grouped.setdefault(link["signal_id"], []).append(link)

    keys = {
        (str(link["symbol"]), str(link["entry_order_id"]))
        for group in grouped.values() for link in group
    }
    projected = {}
    if keys:
        # Import lazily: engine-only deployments and tests must not gain a
        # runtime dependency on the Dashboard until an entry is actually linked.
        from dashboard.autotrade_live.db import get_engine
        from dashboard.autotrade_live.results import load_results
        engine = get_engine()
        try:
            rows = load_results(engine) or []
        finally:
            engine.dispose()
        projected = {
            (str(row.get("symbol")), str(row.get("entry_order_id"))): row
            for row in rows if row.get("entry_order_id")
        }
    feedback = {}
    for review in reviews:
        event_id = review["event_id"]
        group = grouped.get(event_id, [])
        if len(group) > 1:
            feedback[event_id] = {
                "status": "MULTIPLE_LINKED_ENTRIES_UNRESOLVED",
                "entry_order_id": None, "outcome": None, "pnl": None,
                "roi": None, "quality": "AMBIGUOUS_ENTRY_IDENTITY",
                "source": "signal_links",
            }
            continue
        if not group:
            feedback[event_id] = normalize_feedback(None, None)
            continue
        link = group[0]
        # The exact ID and symbol must agree. No approximate signal matching.
        if str(link["symbol"]) != str(review.get("symbol")):
            feedback[event_id] = {
                "status": "SYMBOL_MISMATCH_UNRESOLVED",
                "entry_order_id": str(link["entry_order_id"]),
                "outcome": None, "pnl": None, "roi": None,
                "quality": "INVALID_LINK_IDENTITY", "source": "signal_links",
            }
            continue
        entry_id = str(link["entry_order_id"])
        feedback[event_id] = normalize_feedback(
            projected.get((str(link["symbol"]), entry_id)), entry_id)
    return feedback

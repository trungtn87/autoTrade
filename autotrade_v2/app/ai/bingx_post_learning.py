"""OBSERVE-only BingX DB post-trade learning with immutable evidence fingerprints.

A real BingX fill/close is NEVER inferred from candles. One Gemini analysis
is written per distinct source projection. Revisions coexist rather than
silently rewriting history, and estimates remain explicitly inconclusive.
"""
from __future__ import annotations

import hashlib
import json

MODEL_PROMPT_VERSION = "observe.bingx.post.vi.v1"
QUALITY_VERIFIED = "BINGX_VERIFIED_EXIT"
QUALITY_ESTIMATED = frozenset((
    "BINGX_VERIFIED_EXIT_ESTIMATED_MONEY", "MERGED_POSITION_ESTIMATE",
))
QUALITIES = frozenset((QUALITY_VERIFIED,)) | QUALITY_ESTIMATED
OUTCOMES = frozenset(("WIN", "LOSS", "BREAKEVEN"))


def eligible(review: dict, evidence: dict | None) -> bool:
    if not evidence or review.get("ai_decision") not in ("ALLOW", "BLOCK"):
        return False
    if evidence.get("quality") not in QUALITIES or evidence.get("outcome") not in OUTCOMES:
        return False
    if evidence.get("status") not in ("CLOSED", "ESTIMATED_CLOSED"):
        return False
    if not evidence.get("entry_order_id"):
        return False
    try:
        return int(evidence.get("closed_ms") or 0) > int(review["signal_close_ms"])
    except (TypeError, ValueError, KeyError):
        return False


def evidence_payload(review: dict, evidence: dict) -> dict:
    """Only source-backed facts: no current Binance price, secret, or made-up fill."""
    return {
        "event_id": review["event_id"],
        "signal_close_ms": int(review["signal_close_ms"]),
        "ai_decision": review["ai_decision"],
        "entry_order_id": str(evidence["entry_order_id"]),
        "symbol": review["symbol"], "combo": review["combo"],
        "closed_ms": int(evidence["closed_ms"]),
        "status": evidence["status"], "outcome": evidence["outcome"],
        "quality": evidence["quality"], "source": evidence.get("source"),
        "pnl": str(evidence["pnl"]) if evidence.get("pnl") is not None else None,
        "roi": str(evidence["roi"]) if evidence.get("roi") is not None else None,
        "shared_position": bool(evidence.get("shared_position")),
        "reason": evidence.get("reason"),
    }


def evidence_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def assessment(decision: str, outcome: str, quality: str) -> str:
    """A merged/fee-estimated outcome is never a proven correct AI decision."""
    if quality != QUALITY_VERIFIED or outcome == "BREAKEVEN":
        return "INCONCLUSIVE"
    if decision not in ("ALLOW", "BLOCK") or outcome not in ("WIN", "LOSS"):
        return "INCONCLUSIVE"
    return ("CORRECT" if ((decision == "ALLOW") == (outcome == "WIN"))
            else "INCORRECT")


def prompt_for_review(review: dict, evidence: dict) -> str:
    original = review.get("output_json") or {}
    original = original if isinstance(original, dict) else {}
    raw = review.get("input_json") or {}
    raw = raw if isinstance(raw, dict) else {}
    ctx = {
        "mode": "OBSERVE_POST_BINGX_ONLY",
        "evidence": evidence,
        "pre_trade": {
            "decision": review["ai_decision"],
            "reasoning_verified": original.get("reasoning_verified"),
            "reasoning_ai_opinion": original.get("reasoning_ai_opinion"),
            "risk_flags": (original.get("risk_flags") or [])[:12],
            "signal": raw.get("signal"),
            "signal_time_smc_features": (raw.get("features") or [])[:16],
        },
        "note": "All hindsight conclusions are hypotheses, never modify 20FINAL.",
    }
    return (
        "Bạn là Gemini Analyst CHỈ QUAN SÁT. So sánh đánh giá trước Entry với "
        "kết quả lấy từ BingX database do Dashboard đã đối soát, KHÔNG phải nến "
        "giả lập. Không gợi ý sửa engine trực tiếp. Nêu nguyên nhân/rủi ro hợp lý "
        "nhưng không khẳng định quan hệ nhân quả nếu thiếu chứng cứ. "
        "BINGX_VERIFIED_EXIT chỉ chứng minh Exit theo bản ghi lệnh; không tự "
        "suy diễn phí, funding, trailing hoặc slippage. "
        "BINGX_VERIFIED_EXIT_ESTIMATED_MONEY và MERGED_POSITION_ESTIMATE đều "
        "là ƯỚC TÍNH, vì vậy pre_trade_assessment BẮT BUỘC INCONCLUSIVE, "
        "không được khẳng định đúng/sai chắc chắn. Nếu là kết quả xác minh, "
        "chỉ kết luận dựa vào thắng/thua, không đồng nghĩa mô hình dự đoán "
        "có lợi thế thống kê. Trả lời JSON duy nhất gồm primary_cause (chuỗi), "
        "explanation (chuỗi), contributing_factors (mảng chuỗi), lessons "
        "(mảng tối đa 3 chuỗi), pre_trade_assessment "
        "(CORRECT|INCORRECT|INCONCLUSIVE). Tất cả nội dung diễn giải bằng "
        "tiếng Việt có dấu, không bịa số, không thêm dữ kiện bên ngoài. "
        "DỮ LIỆU:\n" + json.dumps(ctx, ensure_ascii=False, default=str)[:16000]
    )


def parse_post_response(response: dict, evidence: dict) -> dict:
    candidate = (response.get("candidates") or [{}])[0]
    parts = ((candidate.get("content") or {}).get("parts") or [])
    raw = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("post_review_not_object")
    for field in ("primary_cause", "explanation"):
        if not isinstance(decoded.get(field), str):
            raise ValueError("post_review_missing_" + field)
    for field in ("lessons", "contributing_factors"):
        values = decoded.get(field)
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise ValueError("post_review_invalid_" + field)
    if decoded.get("pre_trade_assessment") not in ("CORRECT", "INCORRECT", "INCONCLUSIVE"):
        raise ValueError("post_review_invalid_assessment")
    expected = assessment(evidence["ai_decision"], evidence["outcome"], evidence["quality"])
    return {
        "primary_cause": decoded["primary_cause"][:400],
        "explanation": decoded["explanation"][:1400],
        "contributing_factors": [x[:220] for x in decoded["contributing_factors"][:5]],
        "lessons": [x[:250] for x in decoded["lessons"][:3]],
        "pre_trade_assessment": expected,
        "ai_assessment_raw": decoded["pre_trade_assessment"],
        "quality": evidence["quality"],
        "estimated": evidence["quality"] != QUALITY_VERIFIED,
    }

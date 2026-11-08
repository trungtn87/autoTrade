"""Shared Gemini generation configuration for the read-only AI services.

Gemini 3.x uses thinkingConfig.thinkingLevel; `low` is supported by
Gemini 3.8/3.7/3.6 Flash and reduces latency for observer/chat diagnostics.
Do not pass legacy temperature/topP/topK to Gemini 3.8 Flash.
"""
from __future__ import annotations


def generation_config(model: str, max_tokens: int, mime_type: str | None = None) -> dict:
    config: dict = {"maxOutputTokens": max_tokens}
    if model.startswith("gemini-3"):
        config["thinkingConfig"] = {"thinkingLevel": "low"}
    if mime_type:
        config["responseMimeType"] = mime_type
    return config

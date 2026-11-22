"""Gói nghiên cứu ngoại tuyến V3-18b: Chạy lại thêm/bớt/lọc mã lấy ĐÚNG Lab 11 live làm cơ sở."""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.json"

__all__ = ["DEFAULT_CONFIG_PATH", "PACKAGE_DIR"]

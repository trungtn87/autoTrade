"""Script chạy nghiên cứu V3-18b lấy V3_LAB_11_PRUNE23@11.0.0 làm cơ sở duy nhất (chỉ offline)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.research_v3_18b.__main__ import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("run", "verify"):
        args = ["run", *args]
    raise SystemExit(main(args))

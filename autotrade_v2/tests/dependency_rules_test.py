from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"


def text_files(folder: str):
    return (ROOT / folder).rglob("*.py")


def assert_forbidden(folder: str, forbidden: tuple[str, ...]) -> None:
    violations = []
    for path in text_files(folder):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)} -> {token}")
    assert not violations, "dependency boundary violations:\n" + "\n".join(violations)


def main() -> None:
    assert_forbidden(
        "strategy",
        (
            "import httpx",
            "import requests",
            "import sqlite3",
            "import psycopg",
            "from ..data",
            "from ..execution",
        ),
    )
    assert_forbidden(
        "data",
        (
            "from ..strategy",
            "from ..execution",
        ),
    )
    assert_forbidden(
        "execution",
        (
            "from ..data",
        ),
    )
    print({"ok": True, "dependency_rules": True})


if __name__ == "__main__":
    main()

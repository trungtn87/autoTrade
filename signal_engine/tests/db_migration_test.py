from __future__ import annotations

from scripts.migrate_postgres import exact_match, fingerprint_rows


def main():
    rows = [
        ("BTC-USDT", "15m", 1, 1.0, 2.0, 0.5, 1.5, 10.0, 2),
        ("BTC-USDT", "15m", 3, 1.5, 2.5, 1.0, 2.0, 12.0, 4),
    ]
    c1, h1 = fingerprint_rows(rows)
    c2, h2 = fingerprint_rows(list(rows))
    assert c1 == c2 == 2
    assert h1 == h2

    changed = list(rows)
    changed[1] = tuple(list(changed[1][:-2]) + [13.0, changed[1][-1]])
    _, h3 = fingerprint_rows(changed)
    assert h3 != h1

    source = {"candles": {"count": 2, "sha256": h1}}
    target = {"candles": {"count": 2, "sha256": h1}}
    assert exact_match(source, target)
    target["candles"]["count"] = 3
    assert not exact_match(source, target)
    print("DB MIGRATION TEST PASS")


if __name__ == "__main__":
    main()

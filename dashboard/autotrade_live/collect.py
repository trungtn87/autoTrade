"""Standalone read-only periodic sync: BingX executions + V2 Combo link bridge."""
import argparse
import json
from .bridge import refresh_signal_links
from .db import get_engine, init_db
from .sync import sync


def collect(days=2):
    engine = get_engine()
    init_db(engine)
    mapped = refresh_signal_links(engine)
    bingx = sync(days=days)
    return {'combo_links': mapped, 'bingx': bingx}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(collect(args.days), ensure_ascii=False))

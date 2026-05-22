"""CLI maintenance for Adioranye performance layer."""
from __future__ import annotations

import argparse
import json
from power_features import get_power_store


def main() -> int:
    parser = argparse.ArgumentParser(description="Adioranye DB performance maintenance")
    parser.add_argument("--db", default=".adioranye_power.db")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    args = parser.parse_args()

    store = get_power_store(args.db)
    result = {"dashboard": store.performance_dashboard(days=args.days)}
    if args.clear_cache:
        result["cleared_response_cache"] = store.clear_response_cache()
    result["optimize"] = store.optimize_database(vacuum=bool(args.vacuum))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

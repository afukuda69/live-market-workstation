"""Create (or upgrade in place) the SQLite database.

Usage, from backend/:
    python -m scripts.init_db            # uses DB_PATH or data/market.db
    python -m scripts.init_db --reset    # move the old file aside first
"""
from __future__ import annotations

import argparse
import time

from app.config import Settings
from app.db import connect, init_db


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="rename the existing database to *.bak-<time> first")
    args = p.parse_args()
    s = Settings()
    if args.reset and s.db_path.exists():
        backup = s.db_path.with_suffix(f".bak-{int(time.time())}")
        s.db_path.rename(backup)
        print(f"moved old database to {backup}")
    conn = connect(s.db_path)
    init_db(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"database ready: {s.db_path}")
    print("tables:", ", ".join(t for t in tables if not t.startswith("sqlite_")))
    conn.close()


if __name__ == "__main__":
    main()

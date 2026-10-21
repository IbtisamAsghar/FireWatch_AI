"""Cross-process conservative reservations shared by all weather adapters."""

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class WeatherError(RuntimeError):
    """Fixed public error; provider bodies are never echoed."""


class WeatherBudget:
    def __init__(self, path: Path) -> None:
        self.path = path

    def reserve(self, now: datetime, weight: int) -> None:
        if now.tzinfo is None or not 1 <= weight <= 256:
            raise ValueError("Invalid weather budget reservation")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(UTC).timestamp()
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls (stamp REAL, weight INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY, until REAL)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM calls WHERE stamp <= ?", (stamp - 86400,))
            cooldown = db.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
            if cooldown and cooldown[0] > stamp:
                raise WeatherError("Weather provider cooldown active; retry later.")
            for seconds, limit in ((60, 30), (3600, 300), (86400, 3000)):
                used = db.execute(
                    "SELECT COALESCE(SUM(weight),0) FROM calls WHERE stamp > ?",
                    (stamp - seconds,),
                ).fetchone()[0]
                if used + weight > limit:
                    raise WeatherError("Weather request budget exhausted; retry later.")
            db.execute("INSERT INTO calls VALUES (?,?)", (stamp, weight))
            db.commit()

    def defer(self, now: datetime, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            seconds = 60
        until = now.timestamp() + max(60, seconds)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                "INSERT INTO cooldown VALUES (1,?) ON CONFLICT(id) DO UPDATE "
                "SET until=MAX(until,excluded.until)",
                (until,),
            )

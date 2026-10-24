"""Explicit opt-in, bounded provider check; no database, scoring or saved raw handoff."""

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from firewatch.sources.firms import FirmsAreaRequest, FirmsClient, FirmsPolicy
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest, weather_cell
from firewatch.sources.weather_budget import WeatherBudget


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize bounded live provider calls")
    args = parser.parse_args()
    if not args.live or not os.getenv("FIRMS_MAP_KEY"):
        parser.error("Requires --live and FIRMS_MAP_KEY supplied explicitly in the environment")
    result = {"schema_version": "live-provider-smoke-v1", "firms": "failed", "weather": "failed"}
    try:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory(prefix="fw-live-provider-") as folder:
            root = Path(folder)
            with FirmsClient(
                SecretStr(os.environ["FIRMS_MAP_KEY"]),
                root / "firms",
                policy=FirmsPolicy(max_attempts=1, max_rows=10000),
            ) as firms:
                batch = firms.fetch(
                    FirmsAreaRequest.model_validate(
                        {
                            "product": "VIIRS_NOAA20_NRT",
                            "days": 1,
                            "start_date": (now - timedelta(days=1)).date().isoformat(),
                            "bbox": {"west": -121.5, "south": 39, "east": -121, "north": 39.5},
                        }
                    )
                )
                result["firms"] = "ok_empty" if batch.empty else "ok"
            end = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            weather = OpenMeteoClient(root / "weather", WeatherBudget(root / "budget"))
            try:
                weather.fetch(
                    WeatherRequest(
                        cells=[weather_cell(39.4, -121.4)], start=end - timedelta(hours=2), end=end
                    )
                )
                result["weather"] = "ok"
            finally:
                weather.close()
    except Exception:
        # Provider bodies, credential-bearing URLs and exception chains never enter CI output.
        pass
    output = Path("artifacts/live-provider-smoke.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return int("failed" in result.values())


if __name__ == "__main__":
    raise SystemExit(main())

"""Complete-year synthetic POWER responses, never real provider evidence."""

from datetime import UTC, datetime

import httpx

from firewatch.processing.baselines import BaselineSpec
from firewatch.sources.power import PowerClient, PowerRequest, climate_cell
from firewatch.sources.weather_budget import WeatherBudget

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def request(year=2010, latitude=39.4, longitude=-121.4):
    return PowerRequest(cell=climate_cell(latitude, longitude), year=year)


def payload(req, temperature=10, precipitation=2):
    keys = [day.strftime("%Y%m%d") for day in req.days()]
    return dict(
        type="Feature",
        geometry=dict(type="Point", coordinates=[req.cell.longitude, req.cell.latitude, 100]),
        header=dict(
            time_standard="UTC",
            fill_value=-999.0,
            start=f"{req.year}0101",
            end=f"{req.year}1231",
            sources=["MERRA2"],
            api=dict(version="synthetic"),
        ),
        parameters=dict(T2M=dict(units="C"), PRECTOTCORR=dict(units="mm/day")),
        properties=dict(
            parameter=dict(
                T2M={k: temperature for k in keys}, PRECTOTCORR={k: precipitation for k in keys}
            )
        ),
        messages=[],
    )


def client(tmp_path, *, fail_year=None):
    calls = []

    def handler(r):
        calls.append(r)
        req = PowerRequest(
            cell=climate_cell(float(r.url.params["latitude"]), float(r.url.params["longitude"])),
            year=int(r.url.params["start"][:4]),
        )
        if req.year == fail_year:
            return httpx.Response(503)
        return httpx.Response(200, json=payload(req))

    provider = PowerClient(
        tmp_path / "raw",
        WeatherBudget(tmp_path / "budget.sqlite"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
        sleep=lambda _: None,
    )
    return provider, calls


def batches(tmp_path, spec=None):
    spec = spec or BaselineSpec()
    provider, _ = client(tmp_path)
    return [
        provider.fetch(request(year))
        for year in range(spec.reference_start_year, spec.reference_end_year + 1)
    ]

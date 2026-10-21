# Weather clients and caching

Phase 3.1 supplies source adapters, PostgreSQL caching and temporal selection.
It does not build clusters, climate baselines, feature vectors or pipeline Runs.

## Operator commands

The bounded cell-based CLI is available without new credentials or dependencies:

```powershell
firewatch import-weather --help
firewatch import-weather --latitude 39.4 --longitude -121.4 --start 2024-07-24T01:00:00Z --end 2024-07-25T00:00:00Z
firewatch import-weather --latitude 39.4 --longitude -121.4 --start 2024-07-24T01:00:00Z --end 2024-07-25T00:00:00Z --offline
```

Use the installed `firewatch` command; `firewatch.cli` exposes the Typer app for
embedding and does not execute it when invoked as a Python module.
Repeat latitude/longitude options in matching order for up to eight cells.
Duplicates within a cell are coalesced. Intervals are inclusive whole UTC hours,
at most seven days apart. Requests include full provider calendar days and retain
only the specified hours. Large inventories must be processed in bounded windows.
Region-to-centroid cell enumeration belongs to clustering in Phase 3.3; this command
requires explicit coordinates rather than pretending one sample covers a region.

Historical forecast with `gfs_seamless` is the default. `--product live` uses only
the forecast host, refuses old dates (older than five days), and never requests a
future hour. `--product reanalysis` explicitly selects ERA5 on the archive host.
There is no automatic fallback or substitution. Replay-mode service requests reject
live weather before cache lookup or HTTP. `--offline` prohibits every HTTP call.

## Shared interfaces

- `weather_cell(lat, lon)` maps positions to fixed 0.1-degree centers, anchored at
  (-90,-180), using half-open cells; extreme east/north coordinates clamp to the
  final cell. `grid-0p1-v1` is recorded in cell identity and source provenance.
- `WeatherRequest` bounds cells, UTC hours, product/model, mode and source namespace.
- `LiveWeatherAdapter`, `HistoricalForecastAdapter` and `ReanalysisAdapter` use the
  same `WeatherSeries`/`WeatherRecordInput` contracts and a common bounded client.
- `get_weather(engine, request, client=..., offline=..., retrieval_cutoff=...)`
  reads the cache first and performs network work outside database transactions.
- `weather_at_cutoff(series, T)` selects one cell/product/model/source revision and
  returns the latest nonempty hour ending at or before T, its age, quality flags
  and complete preceding-day rainfall. Split multi-cell series by cell first.

The existing weather/source tables and DTOs are reused. No migration or change to
public API responses is needed. Phase 3.2 will add its own NASA POWER implementation.

## Cache versions and provenance

Requested coordinates are the canonical application cell center. Returned latitude
and longitude are preserved separately because the model grid differs. Returned
locations must be within one degree of their requested center. Provider, explicit
model, product, cell, valid hour and source revision identify cached records.

`--source-version weather-v1` is a cache namespace. Every fetch adds an immutable
UUID revision beneath that namespace. Old weather rows and assets are retained;
updates never overwrite a saved source version. A read selects the newest complete
revision per cell available at its retrieval cutoff. It does not stitch incomplete
fetches. A narrower interval can be read from a larger cached fetch. Namespace,
product, model and grid identities never silently mix. At most 100,000 candidate
revision rows are read; narrow the interval or namespace if that bound is reached.

Live retrievals expire from cache reuse after one hour by default; the Python
service accepts a different positive maximum age. Historical data is reusable
without a TTL. `retrieval_cutoff` freezes source availability independently from
the historical feature cutoff T. If omitted, retrieval uses current time. An offline
historical replay can explicitly use later-retrieved archives; this is retrospective
analysis, not an exact issue-time backtest. Frozen past availability cutoffs cannot
fetch new data. Retain asset IDs/source versions in downstream run manifests.

Raw validated JSON is saved under ignored `data/raw/weather/` using exclusive files
and SHA-256. Source metadata records endpoint, model, interval, cells, grid version,
normalizer, precipitation convention, attribution and CC-BY-4.0. Before persistence,
raw bytes are rehashed and parsed again; changed rows or evidence reject the entire
caller-owned transaction. Re-saving the same batch is idempotent. Failed writes
may leave an unreferenced raw file, but no partial database cache transaction.

## Time, units, rainfall and quality

Requests set GMT, ISO8601, Celsius, m/s and millimeters. Returned time/unit metadata,
array lengths, sorted unique whole hours and coordinates are validated. HTML,
provider errors, redirects and schema/unit mismatches fail with fixed safe messages.

Temperature, humidity, wind and gust are model values at their timestamp.
Precipitation at hour H covers `(H-1h,H]`. For T=12:30, the last completed interval
ends at 12:00; rainfall uses the 24 hourly sums ending from the preceding 13:00
through current 12:00, covering `(T_floor-24h,T_floor]`. It does not include the
incomplete 12:00–13:00 interval. The returned `precipitation_interval_end` makes
that distinction explicit.

Missing timestamps produce null rows with `weather_hour_missing`. Missing/invalid
measurements remain null with field-specific flags; zero precipitation remains a
valid zero. All 24 hourly precipitation values must exist for a numeric rolling
sum. Missing intervals produce null plus `precipitation_24h_incomplete`; no local
interpolation or zero filling occurs. Future values cannot influence selection.

`weather_stale` means selected valid-time age is strictly greater than the configured
three-hour default; it is distinct from live cache retrieval age. Reanalysis is
flagged `fallback_product_reanalysis`; historical inputs are `retrospective_weather`.
Provider-native interpolation cannot be reliably inferred from hourly output, so
records explicitly carry `provider_interpolation_unknown`. The client does not
claim every hourly value is a native hourly model observation.

## Requests and budgets

One synchronous request at a time per client batches up to eight coordinates.
Explicit timeouts, three total attempts, exponential retry waits, an operation
deadline and an 8 MiB decompressed-response bound prevent unbounded fetching.
HTTP 429 records a shared cooldown honoring numeric or HTTP-date Retry-After;
other requests defer while that cooldown is active.

All adapter instances used by the CLI share `data/weather-budget.sqlite`. SQLite
atomic reservations bound weighted usage to 30/minute, 300/hour and 3,000/day across
processes. Every attempt is charged one unit per cell/calendar day, deliberately
more conservative than provider accounting. Reservations are retained on failure.
Use one shared budget path for programmatic clients too. Other applications/IP
users are outside this local budget. A request whose weight exceeds 30 must be
split before fetching; no automatic wait or hidden large import is performed.

Free API use is subject to Open-Meteo noncommercial terms and attribution. No new
API key, paid subscription, scheduler, region activation or automatic seasonal
weather download is introduced.

## Official references

- [Forecast variables, units and preceding-hour sums](https://open-meteo.com/en/docs)
- [Historical forecast semantics](https://open-meteo.com/en/docs/historical-forecast-api)
- [ERA5 reanalysis and units](https://open-meteo.com/en/docs/historical-weather-api)
- [Terms and request limits](https://open-meteo.com/en/terms)

See [Phase 3.1 acceptance](phase-3.1-checklist.md) and
[real historical smoke evidence](phase-3.1-smoke.json).

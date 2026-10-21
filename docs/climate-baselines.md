# Fixed climate context: NASA POWER

Phase 3.2 uses NASA POWER **T2M** (daily mean temperature, C) and **PRECTOTCORR**
(daily precipitation, mm/day). It does not use POWER daily values for weather at
an analysis cutoff, and does not implement clustering or the shared feature builder.

## Frozen reference and computation

`configs/features-v1.yaml` now binds the climate reference to **2010–2019**, ten
complete calendar years before the earliest planned snapshot, 2022-05-01 UTC.
`BaselineSpec` requires 10–30 years, no earlier than POWER daily coverage in 1981,
and rejects a reference period extending into the first snapshot. Changing the
study to an earlier period requires a new compatible reference/configuration.
The reference is an engineering choice for this study, not a WMO 30-year normal.

For each UTC calendar month, pool daily values across all reference years and take
an arithmetic mean of valid daily T2M and PRECTOTCORR values independently. This
weights days equally, including leap days; it does not average yearly monthly means
or compute a monthly rainfall total. Daily mm/day values cover one day, so their
monthly mean is a mean daily rainfall amount, not an hourly amount.

Expected calendar-day count, valid daily sample count and years with valid samples
are stored for each variable/month. A mean is null when fewer than 90% of expected
days are valid (configurable explicitly through `climate_min_valid_fraction`).
Incomplete references retain flags even if they meet the threshold. Invalid/fill
values never become zero or enter a denominator. Genuine zero rain remains zero.
Every requested reference year must be present before monthly publication, even if
some of that year's daily values are missing.

## Cells and provider conventions

`climate_cell(lat,lon)` creates half-open application request bins of **0.5° latitude
by 0.625° longitude**, anchored at (-90,-180). Requests use bin centers; north/east
world boundaries clamp to the final cell. `power-grid-0p5x0p625-v1` is part of identity.
Multiple 0.1-degree weather cells can share one climate reference. The CLI deduplicates
cells before fetching; a configured bbox enumerates each intersecting coarse bin.

This matches the documented scale of POWER meteorology while avoiding requests at
weather-cache resolution. It is an application cache grid, not a claim that the
provider's echoed point coordinates are authoritative native grid centers. Full
provider JSON preserves its geometry, API version, source names and metadata.

Daily POWER defaults to Local Solar Time, so the adapter always requests and
validates **UTC**. A daily value covers `[00:00Z,next-day 00:00Z)`. Returned T2M
`C`, PRECTOTCORR `mm/day`, declared fill sentinel, full-year coverage metadata and
requested point coordinates must match. The sentinel is read from the response;
there is no assumption that -999 is the only possible fill value. Missing date
keys and invalid/null values become flagged nulls. Unexpected dates fail validation.

## Operator usage

From the repository root with its existing virtual environment:

```powershell
.\.venv\Scripts\firewatch.exe build-baselines --latitude 39.4 --longitude -121.4 --config configs/features-v1.yaml
.\.venv\Scripts\firewatch.exe build-baselines --latitude 39.4 --longitude -121.4 --offline
.\.venv\Scripts\firewatch.exe build-baselines --region northern-california --config configs/features-v1.yaml
```

Use explicit paired coordinates or a configured region, never both. Repeat coordinate
options for multiple cells. At most 64 unique cells are allowed per invocation;
larger regions must be divided into explicit chunks. Disabled regions can have
climate context prepared without activating them. Region preparation is optional
bulk work; the recorded real acceptance covers one cell, not the whole region.

`--offline` never fetches data. It can return published baselines or derive them from
already-cached complete annual sources. Missing inputs fail clearly. No new NASA key,
paid subscription, database migration or dependency is needed.

## Cache, checkpoints and immutable definitions

POWER requests are full calendar years for one unique coarse cell, fetched
sequentially. Each completed annual source is checksummed, reparsed and committed
in its own short transaction. Requests occur outside database transactions. If a
later year fails, rerunning reuses earlier years and continues. Baselines publish
all twelve months atomically per cell only after every year is present.

Raw sources live under ignored `data/raw/power/`. Existing `source_assets` hold
request/UTC/grid/hash provenance, and existing `climate_baselines` hold the shared
`ClimateBaseline` DTO values. The newest matching annual asset at the explicit
retrieval cutoff is selected. Its original JSON is rehashed and reparsed before use.
Default availability is current retrieval time, independently of the historical
reference dates. Frozen past cutoffs cannot fetch newer sources.

A deterministic, SHA-256-bound manifest under ignored `data/climate-baselines/`
records the full definition, twelve monthly results and source asset IDs/hashes.
The manifest's availability time is the latest input retrieval time; it is a derived
artifact, not a new NASA download. The source asset ID derives from manifest content.
Identical inputs produce identical manifests and baseline identities. Repeated builds
are idempotent. Reads check manifest integrity and agreement with database values.

A baseline version/reference/cell/month cannot silently change its definition,
threshold, inputs or values. An explicit new baseline version is required for a
changed definition or source set. `get_baseline(...)` requires the declared reference
and version; missing monthly baselines return None, not invented climate values.
Retain the selected baseline version/asset ID in downstream feature lineage.

## Climate context at cutoff T

`climate_context(weather_series, T, climate_cell, baseline)` uses the Phase 3.1 hourly
selector, with one weather cell/product/model/revision. It checks climate-cell/month
agreement and ensures the reference period has fully ended before T.

- `temperature_departure_c = preceding_hour_temperature_c - monthly_mean_daily_temperature_c`.
  It is a departure from a daily climate reference, **not a standardized hourly heat anomaly**.
- `rainfall_deficit_24h_mm = max(0, monthly_mean_daily_precip_mm - preceding_24h_precip_mm)`.
  All 24 preceding hourly intervals must exist. At a non-hour cutoff, the interval
  ends at the last completed UTC hour, exposed in `precipitation_interval_end`.
- Missing baseline, missing reference variable, or incomplete/missing weather yields
  null plus explicit missing flags. Stale weather and reference-coverage flags carry
  through. No daily value covering hours after T is joined as current weather.

Historical sources retrieved later are retrospective climate context. This does not
prove exact original issue-time operational availability or remove dataset bias.

## Provider protection and verification

Fixed HTTPS host, no redirects, JSON/schema validation, 8 MiB response bound,
timeouts, a bounded operation deadline and three total attempts bound requests.
A shared persistent `data/power-budget.sqlite` reserves each annual request attempt
under the same conservative budget mechanism used for weather (30/min, 300/hour,
3,000/day). POWER has its own budget file; unrelated provider budgets do not mix.
429 Retry-After creates a shared cooldown. No unbounded waiting or hidden background
fetch occurs. Large region builds may stop at the budget limit; rerun later to resume.
All programmatic POWER clients should share the same budget path.

See [Phase 3.2 acceptance](phase-3.2-checklist.md) and
[real-data evidence](phase-3.2-smoke.json).

Official references checked for this implementation:

- [POWER Daily API: UTC/LST, parameters and coverage](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)
- [POWER request guidance and meteorology resolution](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/)
- [POWER parameter definitions and community units](https://power.larc.nasa.gov/docs/tutorials/parameters/)

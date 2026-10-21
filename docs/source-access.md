# Phase 2.1 source access and first-region study

Status: bounded FIRMS and weather reconnaissance succeeded on 21 October 2026.
The local MAP_KEY was used without exposing it. Both seasonal NOAA-20 CSV archive
requests were submitted through the authenticated portal. NASA delivery, full
seasonal coverage and human source review remain pending.

## First region and product policy

The selected study candidate is `northern-california`, WGS84 bbox
`west=-122.5, south=38.5, east=-120.0, north=40.5` (5 square degrees).
It is a bounded research subset, not a jurisdiction or full incident perimeter.
Its documented incident origins include
[Mosquito, September 2022](https://www.fire.ca.gov/incidents/2022/9/6/mosquito-fire)
and [Park, July 2024](https://www.fire.ca.gov/incidents/2024/7/24/park-fire).
These establish independent historical activity periods worth sampling. They do
not label individual satellite observations or establish a sufficient ML dataset.

The initial study season is May–October, with archive requests for 2022 and 2024.
This is a bounded acquisition choice, not a claim that fires only occur then.
`configs/regions.yaml` records the bbox, season, requested months, sample dates,
product compatibility, archive status and evidence pointer. The existing Pakistan
demo stays available as an unvalidated illustrative configuration.

Use **NOAA-20 only** for the initial cohort: `VIIRS_NOAA20_NRT` for recent samples,
`VIIRS_NOAA20_SP` for science-quality history. Processing versions remain distinct;
this does not imply NRT and SP are interchangeable. NOAA-21 NRT/SP availability is
recorded separately. Do not pool NOAA-21 into training until overlap comparisons
record timing, spatial overlap, FRP/confidence distributions and revision policy.
NOAA-21 cannot be assumed to exist for the 2022 window. Every source remains a
satellite thermal anomaly, not a verified wildfire.

## Account workflow and submitted archive requests

1. Request a free key using the
   [official FIRMS MAP_KEY form](https://firms.modaps.eosdis.nasa.gov/api/map_key/).
   NASA emails the key. Save it locally as `FIRMS_MAP_KEY` in the ignored `.env`;
   do not paste it into chat, a command-line argument, Git, fixtures or screenshots.
2. For a fresh setup, sign in to the [FIRMS archive portal](https://firms.modaps.eosdis.nasa.gov/download/)
   through Earthdata or email verification. Request **CSV, VIIRS NOAA-20**, the
   bbox above, and separate intervals `2022-04-30`–`2022-11-01` and
   `2024-04-30`–`2024-11-01`. The extra day at each end supports later feature/label
   margins; it does not replace later weather/baseline history requirements.
   Choose standard/science-quality processing where offered and preserve the
   delivered product designation. Save confirmations privately and change
   `archive_status` only when the corresponding account action actually occurs.
3. For a small manual sample, request `2022-09-07`–`2022-09-09` and/or
   `2024-07-25`–`2024-07-27` with the same bbox/product. Keep originals under ignored
   `data/raw/` or outside Git. Do not open/resave the original in a spreadsheet.

The portal sends retrieval instructions when preparation finishes. On 21 October
2026 the user signed in, and the agent submitted both configured NOAA-20 CSV
requests under the user's direction. The authenticated request list showed:

- `828115`: 2022-04-30–2022-11-01, source `J1 VIIRS C2`, status `Submitted`.
- `828116`: 2024-04-30–2024-11-01, source `J1 VIIRS C2`, status `Submitted`.

Both requests use `[-122.5,38.5,-120.0,40.5]`, CSV output and confirmation email.
Do not submit duplicate orders. The [sanitized archive evidence](source-access-archive-requests.json)
records exact parameters and portal timestamps without account email or credentials.
Submission establishes that seasonal archive access is arranged; approval, delivery
and contents have not yet been verified.
The Area API can also supply small historical samples when its **current** SP
availability covers the requested dates; it is not a bulk seasonal downloader.

## Run the bounded probes

From the repository root with the existing Python environment:

```powershell
.\.venv\Scripts\firewatch.exe probe-sources --weather-only
.\.venv\Scripts\firewatch.exe probe-sources
```

Each run creates a fresh directory under `data/raw/source-access/` and prints its
path. The full probe performs one availability request for **ALL** products, then
one recent NOAA-20 NRT day, two three-day NOAA-20 SP samples, and ten historical
weather requests (center and four corners in each historical window). Maximum:
14 HTTP requests; transaction usage may exceed the request count. Only supported
NOAA-20/21 products are retained in the typed availability report. Missing products
are explicit, never inferred from an old example or substituted with another sensor.

NASA documents 1–5 days per Area request and a 5,000-transaction/10-minute default
budget. Large queries can consume multiple transactions. These reconnaissance
requests stay small and stop on 429 without retries. Do not run them in a tight
loop. Production retry/backoff, scheduling and transaction accounting belong to
Phase 2.2. Every sample's dates must fit its actual provider availability interval.

Reports contain raw-file SHA-256 checksums, retrieval time, sanitized request
manifests, product date ranges, fully advertised requested months, actual columns,
observed lexical types, null counts, sensor/confidence/version values, date counts,
and acquisition bounds. Advertised months do **not** prove every day's coverage;
observed months refer only to downloaded sample rows. Copy availability into the
region configuration only after a real successful query, with the dated evidence
pointer. Never fill it from the requested months or incident dates.

CSV inspection preserves acquisition time strings (including `0037`), raw confidence
classes, absent FRP, and original bytes. It checks timestamps, finite measurements,
sensor agreement and the requested bbox/window, and rejects malformed/error bodies.
It does not normalize, assign dedup keys, import records or publish runs. Empty
header-only CSV is a valid no-detection sample and is marked explicitly.

HTTP reads have connect/read timeouts, an elapsed streaming limit, an 8 MiB cap,
no redirects, and credential-safe exceptions. CSV inspection caps at 50,000 rows.
Raw error responses without echoed credentials are retained for private inspection;
responses echoing the key are discarded. Log redaction is installed before requests.

`status=probed` means the configured request sequence succeeded; it does not mean
the whole module, seasonal coverage or model acceptance passed. `status=blocked`
and exit code 1 mean a missing prerequisite, failed check, or deliberately incomplete
weather-only run. Failure reports retain earlier evidence and the safe reason.
The command never enables or seeds a region or connects to the application DB.

## Inspect a manually delivered small archive

```powershell
.\.venv\Scripts\firewatch.exe inspect-firms-sample `
  --path data/raw/mosquito-2022.csv `
  --product VIIRS_NOAA20_SP --start 2022-09-07 --days 3
```

This command requires no database or provider secrets and emits a JSON inspection.
It never modifies the original or trusts a filename as proof of provenance.
Check the delivered archive product, retrieval and request confirmation separately.
Full chunked archive import and normalized quality reports remain Phases 2.3–2.4.

## Recorded provider results (21 October 2026)

The authenticated availability query returned these date ranges: NOAA-20 NRT
2026-07-01–2026-10-21, NOAA-20 SP 2018-04-01–2026-06-30, NOAA-21 NRT
2024-01-17–2026-10-20, and NOAA-21 SP 2023-03-01–2026-06-30. Among the
requested months, NOAA-20 SP covers all twelve requested months across 2022 and 2024; NOAA-21
NRT and SP each cover May–October 2024. The NOAA-21 products do not cover the
2022 sample window. The reported NOAA-20 NRT interval covers no complete requested
calendar month. These date ranges describe product availability only; they do not
prove spatial or daily coverage.

One one-day recent NOAA-20 NRT CSV returned the valid header with zero detection
rows. This is an empty sample, not evidence of a provider outage. The two NOAA-20 SP
historical CSVs were non-empty: 1,367 detections across 7–9 September 2022 and
5,037 detections across 25–27 July 2024. Both contained the expected NOAA-20
VIIRS fields: `latitude`, `longitude`, `bright_ti4`, `scan`, `track`, `acq_date`,
`acq_time`, `satellite`, `instrument`, `confidence`, `version`, `bright_ti5`,
`frp`, and `daynight`; historical files also contained `type`. Latitude and
longitude were numeric text, acquisition dates/categories were text, and acquisition
times were integer text. Brightness and FRP fields contained integer or decimal text.
Observed confidence values were `h`, `l`, and `n`; version was `2`. Exact missing
counts and lexical types are in the evidence record. These are thermal detections,
not independently verified fire perimeters or training labels.

[Sanitized dated source-access evidence](source-access-firms.json) contains actual
availability, sample metadata, weather inspection results, request manifests and
SHA-256 checksums. The raw CSV/JSON responses are preserved locally under
`data/raw/source-access/20261021T092732Z-bb821320/`, which Git ignores; raw
detections are not committed. The evidence record excludes raw rows and credentials.

Ten real Open-Meteo Historical Forecast requests also succeeded at the bbox center
and four corners for both sample windows, using `gfs_seamless`, UTC, and explicit
units. Each of five hourly variables had 72 valid values per point/window: 720
point-hours and 3,600 values total. Returned grid positions, units, hour ordering,
array lengths, finite values and missingness were checked.

These are spatial/date spot checks, not full-season or every-cell coverage.
Historical forecasts do not prove that inputs were available at an operational
prediction cutoff. Keep Open-Meteo attribution; this project uses the non-commercial
service under its [published terms](https://open-meteo.com/en/terms).

## Acceptance boundary

All seven Phase 2.1 source-access requirements now have recorded evidence,
including the seasonal archive requests. The region is `probed` and remains disabled
until independent source review. `archive_status: requested` means both requests
were observed as submitted; it does not mean the archive files are delivered or
complete. Download, checksum, inspect and import the delivered seasonal files in
the later archive phase. Human agreement on shared contracts and operational
readiness remains separate from these source-access acceptance checks.

Official protocol references, checked 21 October 2026:
[availability](https://firms.modaps.eosdis.nasa.gov/api/data_availability/),
[Area API](https://firms.modaps.eosdis.nasa.gov/api/area/),
[archive](https://firms.modaps.eosdis.nasa.gov/download/),
[historical forecasts](https://open-meteo.com/en/docs/historical-forecast-api).

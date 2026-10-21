# Reproducible cluster snapshots

Phase 3.3 creates run-specific satellite observation clusters and past-activity
summaries. It does not identify confirmed wildfire events, track incidents across
dates, compute the complete feature schema, score clusters or publish completed Runs.

## Definitions frozen for v1

`ClusteringSpec` is shared by live, training and replay computation:

- Candidate observations are strictly in **(T-6h,T]**, in aware UTC.
- DBSCAN uses scikit-learn, haversine distance, latitude/longitude in radians,
  **eps = 2 / 6371.0088**, min_samples=1, BallTree and one compute job.
- Every candidate is a core point; isolated observations remain singleton clusters.
  Neighbor distance is inclusive at the library's floating-point boundary. There
  is no deliberate inflation of the 2 km radius.
- The centroid is the unweighted spherical vector mean of member positions,
  preserving antimeridian/polar behavior. A degenerate centroid fails explicitly.
- Activity counts include distinct selected observations within 2 km of the
  centroid in **(T-6h,T]**, **(T-12h,T]** and **(T-24h,T]**. They are not cumulative
  cluster memberships, distinct events or confirmed fire counts.

Inputs are sorted by acquisition time and observation UUID; each cluster's members
are sorted by UUID. Snapshot UUIDs derive from run UUID, complete clustering config
hash and sorted member UUIDs. Cluster output order is sorted by snapshot UUID.
Source revision hashes, scoped input hash, config, NumPy/scikit-learn versions and
result digest are recorded. No current clock, provider request, model or future label
is used in the pure processor. Equal inputs/config/environment produce equal output.

`dbscan-haversine-v1` binds the radius to 2 km. A changed radius requires an explicit
new algorithm version. The same fixed temporal definitions apply in all modes.
Dependency versions are locked for reproducibility; retries reject a changed saved
algorithm environment instead of silently replacing old numeric results. Results
near a distance boundary can be sensitive to floating-point libraries; comparisons
across different platforms should use documented numerical tolerance and reviewed
membership boundaries rather than claim universal byte identity.

## Aggregates and missing values

Each shared `ClusterSnapshot` stores centroid, member IDs, latest member time,
FRP maximum/sum and algorithm version. `ClusterDetails` additionally stores:

- Counts for NOAA-20/NOAA-21 and low/nominal/high/unknown confidence categories.
- Independent centroid activity counts for 6, 12 and 24 hours.
- Valid/missing FRP member counts and maximum member distance from the centroid.
- Explicit quality flags for missing/partial FRP and spatially wide clusters.

FRP aggregates use known member values. If some values are missing, they are partial
aggregates and carry `frp_aggregates_partial`; all missing gives null, never zero.
Genuine zero FRP remains zero. Source confidence classes are preserved categories,
not percentages or probabilities. Different sensors count as different satellite
observations; corrections/products of one physical observation cannot be counted twice.

## Source revision and coverage rules

The repository stage consumes `SelectedDetection` through the existing `RevisionPolicy`.
It never computes corrected measurements from canonical first-seen detection columns.
One product per sensor, one selected physical observation and source revision integrity
are checked before spatial processing. Future observations and the exact T-24h lower
boundary cannot affect the scoped input hash or numerical results.

Two calendar days touch the inclusive 24-hour lookup. Each selected sensor/product
must have successful source request-day coverage across that span. A missing day
fails; it is not interpreted as zero activity. A valid header-only source batch is
an explicit empty response, which still does not prove absence of fires or cloud-free
satellite coverage. The default policy refuses rejected-row batches. An explicitly
permissive programmatic policy propagates `source_rows_rejected` instead of hiding it.
The auto-selector does not silently replace a newer rejected-row snapshot with older
apparently cleaner data.

`freeze_firms_inputs` binds the selected batches/revision hashes to the Run. The
snapshot stage looks back 24 hours, filters exact lower boundaries itself, and uses
only those pins on retry. Later imports and corrected source versions cannot change
a saved Run. A new selection or algorithm definition requires a different Run.

## Database stage and API boundary

`build_snapshots(...)` is a pure typed computation. `build_run_snapshots(session,
run_id, policy, config)` owns no commit; call it inside a caller-owned transaction
for a running Run. It locks only that Run, freezes inputs, computes bounded results,
and writes snapshots, memberships, diagnostics and the snapshot stage status together.
There are no HTTP calls in that transaction.

Existing `cluster_snapshots` and `cluster_members` tables are reused. Additional
sensor/confidence/activity diagnostics are stored as a typed, SHA-256-bound batch in
`runs.input_asset_manifest.clustering`, rather than masquerading as finished feature
records. No database migration or shared API DTO change is needed. Snapshot and
membership writes/verification are batched; one SQL query reads all memberships.

A retry checks config, pinned inputs, saved manifest digest and materialized rows;
it cannot silently repair or overwrite tampered results. Empty results have an
explicit successful manifest, so they can also be retried safely. Concurrent calls
for the same Run serialize and produce one set of rows. Stage failure rolls back
pins, outputs and status together.

Only `stage_status.snapshots` becomes succeeded. The Run remains incomplete and
never advances `live_run_heads`; normal ranked read endpoints still require the
later enrichment/features/scoring/evidence stages and completed Run publication.

## Operator command

The command uses cached database sources only and creates a deterministic replay
stage Run. No NASA request or source download occurs:

```powershell
.\.venv\Scripts\firewatch.exe build-snapshots --region northern-california --as-of 2024-07-25T22:00:00Z --allow-disabled
```

`--allow-disabled` explicitly permits research processing without enabling the region.
The default product is NOAA-20 SP; repeat `--product` for one product per additional
sensor. `--retrieval-cutoff` optionally freezes source availability independently
from historical T. The default uses current source availability and is retrospective,
not an issue-time operational backtest. Future analysis/availability cutoffs fail.
`--config` defaults to `configs/features-v1.yaml`.

Repeated commands reuse the Run when region, cutoff, selected batches, config and
feature version match. A later source selection gets a new research Run. Summary
output includes history/candidate/cluster counts, wide-cluster count, manifest hash
and `pipeline_completed=false`. No scheduler or generic worker is introduced.

Resource bounds default to 100,000 selected history inputs, 10,000 candidates and
1,000,000 neighborhood links. A count-only BallTree preflight bounds DBSCAN's
potentially quadratic neighborhood allocation. Centroid lookups are also bounded.
Dense or oversized requests fail clearly rather than silently dropping observations.

## Representative real-data review and limitation

The real 2024-07-25 22:00 UTC northern California replay stage used 1,731 past
observations, 832 candidates and six clusters. All candidates became members exactly
once. Repeat processing retained six snapshots and 832 memberships with identical
manifest digest. The research region remains disabled and the Run remains running,
with only the snapshot stage complete.

The largest connected component has 687 members and maximum centroid distance
13.877 km; only 21 current observations fall within 2 km of its centroid. Another
139-member component reaches 8.082 km, with 53 current centroid-near observations.
A two-member compact cluster reaches 0.138 km. One singleton has 60 observations
within the preceding twelve-hour centroid window. These are expected consequences
of transitive connectivity and independent centroid lookbacks, not counting errors.

DBSCAN eps is a neighbor-link radius, not a maximum cluster diameter. Chains can
merge separate thermal activity and place a centroid far from many members. Wide
components carry `members_outside_centroid_radius`. These cases require scientific
review before event grouping/label interpretation in Module 4. The implementation
retains the plan's 2 km setting; it does not tune radius on these examples, assign
incident identities or claim that six clusters mean six fires.

See [sanitized real evidence](phase-3.3-smoke.json) and
[Phase 3.3 acceptance](phase-3.3-checklist.md).

Official references:

- [DBSCAN: eps semantics and memory behavior](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html)
- [Haversine: latitude/longitude radians](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.haversine_distances.html)

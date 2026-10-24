# Delivery repair validation

Repairs were made against the `devtalha` working tree based on `b8963f8`.
Validation was run before committing the repairs; no hosted CI result or public
deployment is claimed by this report.

## Implemented repairs

- Windows extended filesystem paths for package reads, release verification,
  staging copies and transfer cleanup, with containment/checksum safeguards retained.
- Portable private PostgreSQL provisioning with explicit instance homes and port
  mismatch rejection; empty-target transactional restore and checksum-verified,
  content-addressed source relocation under existing audit roots.
- Compatibility wrappers for the original preparation/regression entrypoints;
  workspace-relative service restart paths and safe handling of copied PID records.
- Current model/setup documentation separated from historical receipts.
- Rebuilt package parity gate, source-hash build metadata and explicit version-2
  delivery-inventory text/binary semantics, including CI checks.
- Two timestamp aggregate queries per run/replay page, and worker health detection
  of overdue queued work or stale running heartbeats/leases.

## Restored application acceptance

A separate native PostgreSQL instance uses loopback port 55432 and a private
Local AppData directory. The delivered dump was restored into an empty application
database, separate from `firewatch_test`. All 607 source locators matched original
hashes; out-of-root sources were copied unchanged to the allowed raw cache.

Fresh API/worker checks passed: readiness, reviewed model availability, matching
six-hotspot list/GeoJSON/summary, preserved `partial` completeness, all four saved
alert payload/source-artifact audits, operator authentication, idempotent queue
submission and the real worker's historical replay. The database contains 19,771
detections, 288 weather rows, 48 climate baselines and one registered model.
See [the local verification receipt](../output/delivery-repair-readiness.json).

No trained model, frozen evaluation, final-test ledger, canonical alert bytes,
completed historical scores or approval attestations were rewritten. Historical
replay acceptance does not establish live NRT, new-region or weather-sensitive skill.

## Regression and packaging

- Full final PostgreSQL-backed suite: **1,292 passed, zero failures, zero skips**,
  531.91 seconds. The runner exited successfully. See
  [the full regression XML](../output/local-regression.xml).
- Fresh virtual environment using the hashed development lock and the final
  installed wheel (import verified from site-packages): **89 passed**, 18.52 seconds.
  See [installed-wheel regression](../output/installed-wheel-tests.xml).
- Focused long-path, relocation, transfer, registry and cleanup recheck through
  the repaired local runner: **37 passed**, 10.09 seconds. See
  [the portability/cleanup report](../output/cleanup-portability-tests.xml).
- Ruff and formatting: passed (246 Python files). Strict mypy: passed (107 source
  files). Dependency consistency and `pip check`: passed.
- The complete managed release and its runtime inference bundle verify from this
  long Windows workspace. The original 760 long-path evidence files remain intact.
- Rebuilt wheel and source archive match all Python package source files. The
  archive excludes operational/private delivery paths and the circular branch
  inventory; sanitized sample fixtures and README files are retained.
- Scaffold validation passed after Windows path repair. The final branch inventory
  is regenerated after build metadata and report completion, then checksum-verified.

The earlier broad test run passed all 1,289 then-collected cases, but its temporary
harness failed during Windows long-path cleanup. Clean-environment cleanup also
exposed readonly Git fixture files. The runner now handles extended paths and
readonly files in its explicitly owned temporary tree; those leftover directories
were removed. The final reports above supersede those intermediate harness results.
No new final holdout was consumed. The existing TestClient deprecation warning
accounts for the one warning in the full suite.

## Remaining operational scope

No frontend, end-user login or public deployment was added. Live/new-region model
validation needs new held-out evidence. Public HTTPS, production monitoring and
operational backup schedules remain deployment work. Queue-health checks allow
idle queues and do not replace process/container monitoring. Docker-engine and
Solidity acceptance were not rerun in this repair session. The existing TestClient
deprecation warning is retained; locked dependencies were not changed merely to
silence it. Fresh database/provider checks never consumed a new final-test claim.

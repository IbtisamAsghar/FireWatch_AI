# Public API contract acceptance

Phase 1.6 covers all 15 read operation response DTOs, UTC timestamp normalization,
explicit units, null/missing values and baseline probability, bounded pagination,
GeoJSON coordinate order, replay isolation and structured error correlation.
PostgreSQL tests migrate/seed a disposable demo schema and verify cleanup, including
failed startup. Handoff export tests cover all reads, schema fields and secret exclusion.

Run all suites with `python scripts/verify_bootstrap.py` on native Windows setup.
With a privately supplied FIREWATCH_TEST_DATABASE_URL ending in _test, use
`python -m pytest tests/contract`. Pure tests run without services using
`python -m pytest tests/contract -m "not integration"`.

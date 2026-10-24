# E2E

The complete offline journey lives in
`tests/integration/test_pipeline_end_to_end.py` so it shares the migrated,
disposable PostgreSQL fixtures. It covers real importer/worker/API code with
synthetic provider transcripts, cached/missing enrichment and a deterministic
test model. It runs with the default pytest suite; no provider keys are used.

Related crash/outage/model and response-contract suites are mapped in
[CI acceptance](../../docs/ci-and-integration.md). These software fixtures do not
establish real incident performance or production model approval.

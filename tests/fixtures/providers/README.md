# Offline provider transcripts

These saved HTTP bodies are **synthetic**, sanitized protocol examples, not
recordings of actual incidents. No credential or credential-bearing URL is saved.
`firms-area.txt` preserves the FIRMS CSV wire format; `firms-quota.json` preserves
the quota response shape. The pipeline acceptance test passes them through the
real HTTP client, normalizer, PostgreSQL importer and worker using MockTransport.
Weather and POWER response transcripts are generated deterministically by the
existing `weather_factory` and `power_factory` for complete requested intervals.

All standard CI provider calls use these offline transcripts or existing synthetic
factories. Real provider availability is checked only by the separate manual workflow.

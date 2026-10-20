# Synthetic acceptance sample

synthetic-demo-v1.json is invented software test/demo input authored for this
repository under the repository MIT license. Its coordinates, FRP, weather,
priorities and ranking values are illustrative fixtures: no real fire observation,
NASA/Open-Meteo retrieval, model prediction or scientific evidence is supplied.
The file is deliberately small, versioned and explicitly marked synthetic.

The demo migrates a fresh owned schema in a disposable _test database, seeds these
records with deterministic fixture UUIDs, and removes the schema on graceful shutdown.
It never seeds the application/public schema; all demo mutations are blocked.
See docs/demo-walkthrough.md from the repository root. Real downloads belong in
ignored data/raw or data/archive with their own provenance and licenses.

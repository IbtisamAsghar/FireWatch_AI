# FireWatch frontend handoff

openapi.json is the real 18-operation API contract. examples.json contains actual API responses from isolated synthetic fixtures for every read and representative errors. field-dictionary.json includes all schemas, nullability, enums and unit notes. demo-index.json provides UUIDs/paths; manifest.json records versions/checksums.

No token, database credential, real source manifest or private key is included. Protected operations need a separately provisioned server-side operator token. Synthetic scores are not predictions. See repository docs/frontend-handoff.md, docs/operator-api.md and docs/demo-walkthrough.md.

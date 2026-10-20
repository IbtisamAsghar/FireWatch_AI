# Alembic migrations

Member 1 owns revision numbering. `0001_core_schema.py` freezes the 19-table
schema; `0002_integrity_guards.py` installs immutable-alert/completed-output and
completed-live publication guards. Future revisions must preserve released ones.

Run `alembic upgrade head`, `alembic current`, and `alembic check` from the repo
root using the project virtual environment. Credentials come from validated
settings, not alembic.ini. Offline `alembic upgrade head --sql` needs no secrets.
Tests provide an explicit isolated connection and never downgrade application
data. Destructive downgrades belong only in disposable/verified restore workflows.

See [database ownership and acceptance](../docs/database-schema.md) and
[native setup](../docs/local-development.md). Custom PostgreSQL functions/triggers
must be reviewed explicitly; `alembic check` only compares table/model metadata.

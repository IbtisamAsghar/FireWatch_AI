# devtalha delivery and rebuild procedure

This branch contains backend source, real historical inputs, prepared snapshots,
labels, splits, frozen evaluation, final-test ledger, reviewed historical model,
replay bundles, a consistent database dump, examples and package builds.
The [current model card](model-card.md) defines scope and limitations. Use
[the fresh-install guide](local-real-readiness.md) to restore the demonstration;
original-machine service records and historical receipts are not startup state.

## Windows path portability

Enable `git config core.longpaths true` before checking out the long model evidence
paths. Runtime package readers, managed-release verification and transfers, and
scaffold checks use explicit Windows extended paths for filesystem operations.
These prefixes are never written into canonical evidence or package identities.
Containment, symlink, size and checksum protections remain in force. A short
checkout is convenient but no machine-wide long-path policy change is required.

## Build and validate packages

Install the hashed development lock, then build from the reviewed working tree:

```powershell
.\.venv\Scripts\python.exe -m build --no-isolation
.\.venv\Scripts\python.exe scripts/check_distribution.py
.\.venv\Scripts\python.exe scripts/release_metadata.py
```

Both wheel and source archive must match every Python package source file.
`check_distribution.py` detects missing, extra and changed modules. CI checks new
builds before exporting metadata. Metadata records source-file SHA-256 values,
artifact digests, Git base revision and whether the working tree is dirty. A
working-tree repair build is not represented as a committed or hosted release.
The source archive excludes the branch inventory (which hashes the archive),
operational reports, local runtime records and data/model delivery assets
(sanitized sample fixtures and README files are retained). This
avoids a circular inventory/archive digest and keeps operational files separate.
For an existing prototype `0.1.0` installation, use `pip install --force-reinstall
--no-deps <rebuilt-wheel>`; these local repairs retain the prototype version.
Install the wheel into a clean environment with the same lock to verify runtime,
independent-holdout and quota behavior before publication.

## Delivery inventory

`branch-delivery-inventory.json` version 2 records SHA-256 values for reviewed
tracked files and explicitly included new source files. It excludes itself to
avoid a circular digest. Its hash semantics are `lf-normalized-text-exact-binary-v1`:

- Paths marked `-text` in Git attributes, including data, models and distributions,
  are hashed exactly as bytes. Original model/evaluation/evidence bytes stay pinned.
- Other text files use LF-normalized content so CRLF checkout does not invalidate
  their source hashes. Binary files identified by a NUL in the initial Git-style
  detection window retain their exact bytes.
- `total_bytes` is informational size at generation, not a cross-platform invariant.

After all reviewed files and built artifacts are finalized, regenerate and verify:

```powershell
.\.venv\Scripts\python.exe scripts/delivery_inventory.py --write
.\.venv\Scripts\python.exe scripts/delivery_inventory.py
.\.venv\Scripts\python.exe scripts/check_scaffold.py
```

New files should be tracked before generation, or explicitly supplied with repeated
`--include path/to/reviewed-source.py` arguments. Ignored credentials, runtime data,
cache downloads and test scratch directories are not automatically included.
Do not regenerate frozen model/evaluation manifests to accommodate source edits.
The branch delivery inventory is separate from those immutable scientific artifacts.

## Database and operations

`data/database/firewatch.dump` is a custom-format PostgreSQL snapshot without
ownership/privilege assignments. The portable preparation helper restores only
into an empty database, in one transaction, without `--clean`. It derives source
locators from the current workspace and verifies each source hash; out-of-cache
historical originals are installed under the existing allowed raw-cache root.
Completed runs, canonical payloads, scores and model approval records are not edited.
Use a disposable `_test` database for regressions, never the application database.

Worker `--health` now rejects overdue queued work and stale running heartbeats or
leases. `WORKER_HEALTH_MAX_LAG_SECONDS` defaults to 600; allow expected provider
backoff when selecting a deployment-specific bound. An empty queue is idle/healthy;
this does not prove worker process liveness. Monitor container/process state as well.
Run-history and replay-history timestamps use two aggregate queries per page rather
than two additional queries for every returned row.

No frontend, user-account system, public HTTPS deployment, new-region/live model
validation or enrichment-sensitive model skill is implied by these repairs.
[Repair validation](delivery-repair-validation.md) records the fresh local checks.

Excluded: private `.env` files, bootstrap passwords, signing keys, installed runtimes,
dependency caches and running PostgreSQL internal files. Git exclusions remain the
default for future unreviewed data/model downloads. Secret scanning still applies
to every tracked file; artifact checksums are integrity references, not credentials.

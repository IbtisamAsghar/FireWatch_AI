# Complete devtalha branch delivery

This branch intentionally includes the real source data, prepared snapshots and
labels, splits, training/evaluation artifacts, one-shot final-test ledger, active
approved model release, replay bundles, API examples, test results, PDF guide,
package builds and local recovery/operator scripts requested by Talha.

`branch-delivery-inventory.json` records file hashes for this handoff. Published
data/model paths use `-text` Git attributes so checkout cannot alter their exact
bytes or invalidate evidence hashes. This repository requires long-path support
on Windows: `git config core.longpaths true` before checkout when needed.

`data/database/firewatch.dump` is a consistent PostgreSQL custom-format snapshot
of the application database, exported without ownership/privilege assignments.
Restore it into a separately provisioned application database with `pg_restore`.
Never restore over existing data without reviewing the target first. Source/model
paths in this snapshot reflect the original Windows workspace; rebase or reinstall
the matching managed artifacts before serving from another host or directory.

Excluded from publication: `.env`, `.env.compose`, old environment backups,
database bootstrap credentials, private keys, installed virtual environments and
third-party runtimes, dependency/test caches, Python bytecode, scratch files and
running PostgreSQL internal files. They are not portable project source; the
consistent application dump supplies database content without bootstrap secrets.
Local environment examples remain included. Supply fresh credentials locally.

Data/model ignore rules remain defaults for future unreviewed downloads. This
explicit handoff force-adds reviewed files and the scaffold permits only their
exact pinned artifact hashes. Credential and executable-model restrictions remain.
Secret scanning still applies to every tracked delivery file.

# Pre-final-test audit correction

Recorded 24 October 2026, before the independent 2025 holdout was claimed or any
final predictions or metrics were computed. The original
[protocol](2025-independent-validation-protocol.md), data, features, model search,
threshold objectives and release minimums are unchanged.

The split implementation originally bounded each forward-CV fold by its own end.
When the new final season starts after a gap, the last CV fold ends later than the
last development block. Boundary-purge exclusions are deliberately reconsidered
inside CV, so this could admit late-2024 feature/label support or source events
continuing into the already consumed original test period.

The generic correction also caps each fold by the end of the final development
block minus its purge. Both the feature cutoff and the whole-source event end
must precede that boundary. Adjacent-block original v1 behavior is unchanged;
gapped experiments now retain the same development-end protection. Tests cover
both a late target window and an earlier snapshot whose event continues into
the old holdout.

The first development artifacts are retained for audit and abandoned for release.
New splits, training and development artifacts use `independent-2025-v2` paths.
The independent 2025 ledger must still be unclaimed before pinning this amendment.
No final outcomes informed this correction, and a final failure will not authorize
another adjustment on the same holdout. Independent human release review remains
mandatory after all performance gates pass.

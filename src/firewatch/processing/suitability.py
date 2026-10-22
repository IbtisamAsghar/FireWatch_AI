"""Conservative input eligibility for the planned 2 km centroid target; no labels."""

from firewatch.processing.clusters import ClusterDetails
from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.schemas.contracts import FeatureSet

POLICY = "compact-centroid-inputs-v1"


def centroid_target_exclusions(detail: ClusterDetails, feature: FeatureSet) -> list[str]:
    """Return exclusion reasons; an empty list does not establish training readiness.

    All current members must fit the proposed future-label radius so a whole-cluster
    FRP baseline cannot describe a different area. Require complete observed FRP.
    This policy does not change saved clusters, feature hashes, scores or evidence.
    """
    detail = ClusterDetails.model_validate(detail.model_dump())
    feature = validate_feature_set(feature)
    snapshot = detail.snapshot
    if (
        feature.run_id,
        feature.snapshot_id,
        feature.as_of,
        feature.mode,
        feature.feature_version,
    ) != (
        snapshot.run_id,
        snapshot.snapshot_id,
        snapshot.as_of,
        snapshot.mode,
        snapshot.feature_version,
    ):
        raise ValueError("Target input snapshot/feature context mismatch")
    values = FeatureValues.model_validate(feature.values)
    reasons = []
    if detail.member_radius_max_km > 2:
        reasons.append("members_outside_target_radius")
    if values.frp_max_mw is None:
        reasons.append("missing_current_frp")
    if detail.frp_missing_members:
        reasons.append("incomplete_current_frp")
    return reasons

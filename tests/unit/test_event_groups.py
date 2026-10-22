"""Observation-only event matching, deterministic aliases and explicit review operations."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from firewatch.ml.events import (
    EventDecision,
    EventObservation,
    EventReview,
    EventSpec,
    SeparateDecision,
    build_event_catalog,
    reviewed_groups,
)
from firewatch.processing.clusters import EARTH_RADIUS_KM
from firewatch.sources.firms_normalize import digest

T = datetime(2024, 7, 1, tzinfo=UTC)


def point(i, *, km=0, hours=0, longitude=None):
    return EventObservation(
        physical_key=digest(dict(i=i)),
        acquired_at=T + timedelta(hours=hours),
        latitude=0,
        longitude=longitude if longitude is not None else math.degrees(km / EARTH_RADIUS_KM),
        revision_hashes=[digest(dict(revision=i))],
    )


@pytest.mark.parametrize(
    "km,hours,groups", [(4, 48, 1), (4.00001, 48, 2), (4, 48.00001, 2), (0, 0, 1), (0, 73, 2)]
)
def test_closed_event_space_time_boundaries(km, hours, groups):
    catalog = build_event_catalog([point(1), point(2, km=km, hours=hours)], "a" * 64)
    assert len(catalog.groups) == groups


def test_connected_chains_form_whole_period_and_input_order_is_irrelevant():
    rows = [point(1), point(2, km=3, hours=24), point(3, km=6, hours=48)]
    first = build_event_catalog(rows, "a" * 64)
    assert len(first.groups) == 1 and len(first.groups[0].physical_keys) == 3
    assert build_event_catalog(list(reversed(rows)), "a" * 64) == first


def test_antimeridian_and_high_latitude_are_spherical():
    rows = [point(1, longitude=179.99), point(2, longitude=-179.99)]
    assert len(build_event_catalog(rows, "a" * 64).groups) == 1
    rows[0].latitude = rows[1].latitude = 89
    assert len(build_event_catalog(rows, "a" * 64).groups) == 1


def test_revision_aliases_do_not_change_membership_or_event_identity():
    first = build_event_catalog([point(1), point(2, km=1)], "a" * 64)
    rows = [point(1), point(2, km=1)]
    rows[0].revision_hashes.append("b" * 64)
    changed = build_event_catalog(rows, "a" * 64)
    assert changed.groups == first.groups and changed.sha256 != first.sha256


@pytest.mark.parametrize(
    "change", ["physical_duplicate", "alias_duplicate", "oversized", "version"]
)
def test_invalid_catalog_inputs_fail(change):
    rows = [point(1), point(2)]
    spec = EventSpec()
    if change == "physical_duplicate":
        rows[1].physical_key = rows[0].physical_key
    elif change == "alias_duplicate":
        rows[1].revision_hashes = rows[0].revision_hashes
    elif change == "oversized":
        spec.max_observations = 1
    else:
        spec.radius_km = 5
    with pytest.raises(ValueError):
        build_event_catalog(rows, "a" * 64, spec)


def test_dense_work_is_bounded():
    with pytest.raises(ValueError, match="bound"):
        build_event_catalog([point(i) for i in range(5)], "a" * 64, EventSpec(max_neighbor_links=1))


def test_near_split_requires_explicit_merge_or_separation_review():
    catalog = build_event_catalog([point(1), point(2, km=5, hours=50)], "a" * 64)
    assert len(catalog.groups) == 2 and len(catalog.near_misses) == 1
    ids = [g.event_id for g in catalog.groups]
    review = EventReview(
        catalog_sha256=catalog.sha256,
        reviewer="synthetic",
        decisions={
            key: EventDecision(decision="accept", reason="Synthetic inspection") for key in ids
        },
        note="Synthetic ambiguous neighboring periods",
    )
    _, effective = reviewed_groups(catalog, review)
    assert {status for _, status in effective.values()} == {"pending"}
    review.separate_pairs = [
        SeparateDecision(left=ids[0], right=ids[1], reason="Synthetic distinct fire periods")
    ]
    _, effective = reviewed_groups(catalog, review)
    assert {status for _, status in effective.values()} == {"accept"}
    review.separate_pairs = []
    review.merge_sets = [ids]
    _, effective = reviewed_groups(catalog, review)
    assert len(effective) == 1 and next(iter(effective.values()))[1] == "accept"


@pytest.mark.parametrize(
    "invalid", ["hash", "unknown_id", "overlapping_merge", "merged_separation"]
)
def test_review_binding_and_merge_validation(invalid):
    catalog = build_event_catalog([point(1), point(2, km=5, hours=50)], "a" * 64)
    ids = [g.event_id for g in catalog.groups]
    review = EventReview(
        catalog_sha256=catalog.sha256,
        reviewer="synthetic",
        decisions={key: EventDecision(decision="accept", reason="Synthetic") for key in ids},
        note="Synthetic",
    )
    if invalid == "hash":
        review.catalog_sha256 = "0" * 64
    elif invalid == "unknown_id":
        review.merge_sets = [[ids[0], "0" * 64]]
    elif invalid == "overlapping_merge":
        review.merge_sets = [ids, ids]
    else:
        review.merge_sets = [ids]
        review.separate_pairs = [
            SeparateDecision(left=ids[0], right=ids[1], reason="Contradictory")
        ]
    with pytest.raises(ValueError):
        reviewed_groups(catalog, review)


def test_unreviewed_and_excluded_groups_are_not_approved_events():
    catalog = build_event_catalog([point(1)], "a" * 64)
    _, groups = reviewed_groups(catalog, None)
    assert next(iter(groups.values()))[1] == "pending"
    review = EventReview(
        catalog_sha256=catalog.sha256,
        reviewer="synthetic",
        decisions={
            catalog.groups[0].event_id: EventDecision(decision="exclude", reason="Ambiguous")
        },
        note="Synthetic",
    )
    _, groups = reviewed_groups(catalog, review)
    assert next(iter(groups.values()))[1] == "exclude"

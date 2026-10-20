"""Bounded operator requests and trusted replay configuration."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from firewatch.schemas.api import APIResponse
from firewatch.schemas.common import DTO, JobStatus, JobType, UTCDateTime, Version

Slug = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]


class RefreshRequest(DTO):
    region_id: UUID
    window_start: UTCDateTime
    window_end: UTCDateTime

    @model_validator(mode="after")
    def ordered_window(self) -> Self:
        if self.window_start >= self.window_end:
            raise ValueError("Refresh window must be ordered")
        return self


class ReplayRequest(DTO):
    scenario_id: Slug


class AnchorRequest(DTO):
    """No client-selected chain, contract, digest, private key or RPC URL."""


class JobAccepted(APIResponse):
    job_id: UUID
    type: JobType
    status: JobStatus
    available_at: UTCDateTime
    result_run_id: UUID | None


class ReplayScenario(DTO):
    id: Slug
    name: Annotated[str, Field(min_length=1, max_length=200)]
    region_name: Slug
    window_start: UTCDateTime
    window_end: UTCDateTime
    scorer_version: Version
    feature_version: Version
    asset_ids: Annotated[list[UUID], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def valid_scenario(self) -> Self:
        if self.window_start >= self.window_end or len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Scenario window must be ordered and asset IDs unique")
        return self


class ReplayConfig(DTO):
    schema_version: Literal["replays-v1"]
    scenarios: Annotated[list[ReplayScenario], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_scenarios(self) -> Self:
        if len({item.id for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("Replay scenario IDs must be unique")
        return self

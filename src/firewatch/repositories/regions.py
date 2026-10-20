"""Validated, atomic region seeding without hardcoded database edits."""

from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firewatch.db.models import Region
from firewatch.schemas.contracts import RegionConfig


def load_regions(path: Path) -> RegionConfig:
    if path.stat().st_size > 256 * 1024:
        raise ValueError("Region configuration exceeds 256 KiB")
    return RegionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def seed_regions(session: Session, configuration: RegionConfig) -> list[UUID]:
    """Caller owns the transaction; existing UUIDs and omitted regions are preserved."""
    identifiers: list[UUID] = []
    for definition in configuration.regions:
        values = {
            "name": definition.name,
            "enabled": definition.enabled,
            "description": definition.description,
            **definition.bbox.model_dump(),
        }
        statement = insert(Region).values(**values)
        upsert = statement.on_conflict_do_update(
            index_elements=[Region.name], set_=values
        ).returning(Region.id)
        identifiers.append(session.execute(upsert).scalar_one())
    return identifiers

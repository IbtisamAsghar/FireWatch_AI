"""Audited model activation, retained rollback versions and immutable approval pins.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_activation",
        sa.Column("name", sa.String(20), primary_key=True),
        sa.Column("active_model_id", sa.Uuid(), sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("previous_model_id", sa.Uuid(), sa.ForeignKey("model_versions.id")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("name='default' AND revision>=1", name="singleton_revision"),
    )
    op.create_table(
        "model_promotions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("previous_model_id", sa.Uuid(), sa.ForeignKey("model_versions.id")),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("rationale", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("action IN ('promote','rollback')", name="action"),
        sa.CheckConstraint("release_sha256 ~ '^[0-9a-f]{64}$'", name="release_hash"),
    )
    op.execute("""
    CREATE FUNCTION firewatch_guard_model_registry() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME='model_promotions' THEN
            RAISE EXCEPTION 'Model approval audit records are immutable' USING ERRCODE='23514';
        END IF;
        IF OLD.training_manifest->>'schema_version'='model-registry-v1'
            AND OLD.status IN ('approved','retired') THEN
            IF TG_OP='DELETE' OR (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status')
                OR (NEW.status IS DISTINCT FROM OLD.status AND
                    NOT (OLD.status='approved' AND NEW.status='retired')) THEN
                RAISE EXCEPTION 'Approved model identities and artifacts are immutable' USING ERRCODE='23514';
            END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER model_identity_guard BEFORE UPDATE OR DELETE ON model_versions
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_model_registry();
    CREATE TRIGGER model_promotions_immutable BEFORE UPDATE OR DELETE ON model_promotions
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_model_registry();
    CREATE TRIGGER model_promotions_no_truncate BEFORE TRUNCATE ON model_promotions
        FOR EACH STATEMENT EXECUTE FUNCTION firewatch_immutable_alert();
    CREATE FUNCTION firewatch_guard_model_activation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM model_versions WHERE id=NEW.active_model_id AND status='approved'
            AND training_manifest->>'schema_version'='model-registry-v1') THEN
            RAISE EXCEPTION 'Activation requires a registry-approved model' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NEW.revision != OLD.revision+1 THEN
            RAISE EXCEPTION 'Activation revision must advance once' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER model_activation_guard BEFORE INSERT OR UPDATE ON model_activation
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_model_activation();
    CREATE FUNCTION firewatch_guard_scorer_pin() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE pin jsonb; registered boolean;
    BEGIN
        IF TG_TABLE_NAME='runs' THEN
            IF TG_OP='UPDATE' AND OLD.input_asset_manifest->'orchestration' ? 'scorer' THEN
                IF NEW.input_asset_manifest->'orchestration' IS DISTINCT FROM OLD.input_asset_manifest->'orchestration'
                    OR NEW.config_hash != OLD.config_hash OR NEW.scorer_version != OLD.scorer_version THEN
                    RAISE EXCEPTION 'Run approval pins are immutable' USING ERRCODE='23514';
                END IF;
            END IF;
            pin := NEW.input_asset_manifest->'orchestration'->'scorer';
            IF pin IS NOT NULL AND (TG_OP='INSERT' OR OLD.input_asset_manifest->'orchestration'->'scorer' IS NULL)
                AND NOT EXISTS (SELECT 1 FROM model_versions v WHERE
                v.id=(pin->>'model_id')::uuid AND v.version=NEW.scorer_version
                AND v.artifact_hash=pin->>'manifest_sha256' AND v.status='approved'
                AND v.training_manifest->>'approval_reference'=pin->>'approval_reference') THEN
                RAISE EXCEPTION 'Run model approval pin is invalid' USING ERRCODE='23514';
            END IF;
        ELSE
            SELECT input_asset_manifest->'orchestration'->'scorer' INTO pin FROM runs WHERE id=NEW.run_id;
            SELECT training_manifest->>'schema_version'='model-registry-v1' INTO registered
                FROM model_versions WHERE id=NEW.model_id;
            IF pin IS NOT NULL OR registered THEN
                IF pin IS NULL OR NEW.model_id IS NULL OR NEW.model_id != (pin->>'model_id')::uuid
                    OR NEW.scorer_version != pin->>'scorer_version'
                    OR NOT EXISTS (SELECT 1 FROM model_versions WHERE id=NEW.model_id AND status='approved') THEN
                    RAISE EXCEPTION 'Score differs from run approved model' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER runs_scorer_pin BEFORE INSERT OR UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_scorer_pin();
    CREATE TRIGGER scores_scorer_pin BEFORE INSERT OR UPDATE ON scores
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_scorer_pin();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER scores_scorer_pin ON scores")
    op.execute("DROP TRIGGER runs_scorer_pin ON runs")
    op.execute("DROP FUNCTION firewatch_guard_scorer_pin()")
    op.execute("DROP TRIGGER model_activation_guard ON model_activation")
    op.execute("DROP FUNCTION firewatch_guard_model_activation()")
    op.execute("DROP TRIGGER model_identity_guard ON model_versions")
    op.execute("DROP TRIGGER model_promotions_immutable ON model_promotions")
    op.execute("DROP TRIGGER model_promotions_no_truncate ON model_promotions")
    op.execute("DROP FUNCTION firewatch_guard_model_registry()")
    op.drop_table("model_promotions")
    op.drop_table("model_activation")

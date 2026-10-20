"""Protect immutable evidence and atomically publish complete live runs.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION firewatch_immutable_alert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Saved alert evidence is immutable' USING ERRCODE='23514';
    END $$;
    CREATE TRIGGER alerts_immutable_row BEFORE UPDATE OR DELETE ON alerts
        FOR EACH ROW EXECUTE FUNCTION firewatch_immutable_alert();
    CREATE TRIGGER alerts_immutable_truncate BEFORE TRUNCATE ON alerts
        FOR EACH STATEMENT EXECUTE FUNCTION firewatch_immutable_alert();
    """)
    op.execute("""
    CREATE FUNCTION firewatch_guard_completion() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            IF OLD.status='completed' THEN
                RAISE EXCEPTION 'Completed runs are immutable' USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF TG_OP='UPDATE' AND OLD.status='completed' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'Completed runs are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status='completed' THEN
            IF NOT NEW.stage_status @> '{"ingest":"succeeded","snapshots":"succeeded",
                "enrichment":"succeeded","features":"succeeded","scoring":"succeeded",
                "evidence":"succeeded"}'::jsonb THEN
                RAISE EXCEPTION 'All required stages must succeed before completion'
                    USING ERRCODE='23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM cluster_snapshots c WHERE c.run_id=NEW.id AND (
                    c.member_count != (SELECT count(*) FROM cluster_members m WHERE m.snapshot_id=c.id)
                    OR c.latest_detection_at IS DISTINCT FROM (
                        SELECT max(d.acquired_at) FROM cluster_members m
                        JOIN detections d ON d.id=m.detection_id WHERE m.snapshot_id=c.id)
                    OR NOT EXISTS (SELECT 1 FROM feature_sets f WHERE f.snapshot_id=c.id
                        AND f.run_id=NEW.id AND f.feature_version=NEW.feature_version)
                    OR NOT EXISTS (SELECT 1 FROM scores s WHERE s.snapshot_id=c.id
                        AND s.run_id=NEW.id AND s.scorer_version=NEW.scorer_version)
                )
            ) THEN
                RAISE EXCEPTION 'Run has incomplete snapshot memberships, features or scores'
                    USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM scores s WHERE s.run_id=NEW.id AND s.priority='CRITICAL'
                       AND s.scorer_version=NEW.scorer_version
                       AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.score_id=s.id)) THEN
                RAISE EXCEPTION 'Critical scores require saved evidence before completion'
                    USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER runs_completion BEFORE INSERT OR UPDATE OR DELETE ON runs
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_completion();
    """)
    op.execute("""
    CREATE FUNCTION firewatch_guard_live_head() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE candidate runs%ROWTYPE; old_cutoff timestamptz;
    BEGIN
        SELECT * INTO candidate FROM runs WHERE id=NEW.run_id FOR SHARE;
        IF NOT FOUND OR candidate.region_id != NEW.region_id
            OR candidate.mode != 'live' OR candidate.status != 'completed' THEN
            RAISE EXCEPTION 'Latest live must reference a completed live run of the same region'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND OLD.run_id != NEW.run_id THEN
            SELECT as_of INTO old_cutoff FROM runs WHERE id=OLD.run_id;
            IF candidate.as_of <= old_cutoff THEN
                RAISE EXCEPTION 'Latest live cutoff cannot move backwards or replace a tie'
                    USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER live_head_complete BEFORE INSERT OR UPDATE ON live_run_heads
        FOR EACH ROW EXECUTE FUNCTION firewatch_guard_live_head();
    """)
    op.execute("""
    CREATE FUNCTION firewatch_guard_output() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE old_run uuid; new_run uuid; target uuid; state varchar; cutoff timestamptz;
            observation_time timestamptz; belongs boolean;
    BEGIN
        IF TG_OP != 'INSERT' THEN
            IF TG_TABLE_NAME='cluster_members' THEN
                SELECT run_id INTO old_run FROM cluster_snapshots WHERE id=OLD.snapshot_id;
            ELSE old_run := (to_jsonb(OLD)->>'run_id')::uuid;
            END IF;
        END IF;
        IF TG_OP != 'DELETE' THEN
            IF TG_TABLE_NAME='cluster_members' THEN
                SELECT run_id INTO new_run FROM cluster_snapshots WHERE id=NEW.snapshot_id;
            ELSE new_run := (to_jsonb(NEW)->>'run_id')::uuid;
            END IF;
        END IF;
        FOR target IN SELECT DISTINCT unnest(ARRAY[old_run,new_run]) LOOP
            IF target IS NULL THEN CONTINUE; END IF;
            SELECT status, as_of INTO state, cutoff FROM runs WHERE id=target FOR SHARE;
            IF state='completed' THEN
                RAISE EXCEPTION 'Completed run outputs are immutable' USING ERRCODE='23514';
            END IF;
        END LOOP;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        SELECT as_of INTO cutoff FROM runs WHERE id=new_run;
        IF TG_TABLE_NAME='cluster_snapshots' THEN
            IF NEW.latest_detection_at > cutoff THEN
                RAISE EXCEPTION 'Snapshot observations must not follow cutoff' USING ERRCODE='23514';
            END IF;
        ELSIF TG_TABLE_NAME='cluster_members' THEN
            SELECT d.acquired_at, EXISTS (
                SELECT 1 FROM detection_regions dr JOIN runs r ON r.region_id=dr.region_id
                WHERE dr.detection_id=d.id AND r.id=new_run
            ) INTO observation_time, belongs FROM detections d WHERE d.id=NEW.detection_id;
            IF observation_time > cutoff OR NOT belongs THEN
                RAISE EXCEPTION 'Cluster member is outside the run region or observation cutoff'
                    USING ERRCODE='23514';
            END IF;
        ELSIF TG_TABLE_NAME='scores' THEN
            IF NEW.scored_at < cutoff THEN
                RAISE EXCEPTION 'Scoring time precedes feature cutoff' USING ERRCODE='23514';
            END IF;
            IF NEW.model_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM model_versions v JOIN runs r ON r.id=NEW.run_id
                WHERE v.id=NEW.model_id AND v.version=NEW.scorer_version
                AND v.feature_version=r.feature_version
            ) THEN
                RAISE EXCEPTION 'Model feature version does not match the run'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.score_kind='probability' AND NOT EXISTS (
                SELECT 1 FROM model_versions v WHERE v.id=NEW.model_id
                AND v.version=NEW.scorer_version AND v.probability_supported
            ) THEN
                RAISE EXCEPTION 'Model release does not support probability claims' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    """)
    for table in ("cluster_snapshots", "cluster_members", "feature_sets", "scores", "alerts"):
        op.execute(
            f"CREATE TRIGGER {table}_run_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION firewatch_guard_output()"
        )
    op.execute("""
    CREATE FUNCTION firewatch_guard_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM runs WHERE status='completed') THEN
            RAISE EXCEPTION 'Cannot truncate completed run data' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END $$;
    """)
    for table in ("runs", "cluster_snapshots", "cluster_members", "feature_sets", "scores"):
        op.execute(
            f"CREATE TRIGGER {table}_truncate_guard BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION firewatch_guard_truncate()"
        )


def downgrade() -> None:
    for table in ("runs", "cluster_snapshots", "cluster_members", "feature_sets", "scores"):
        op.execute(f"DROP TRIGGER {table}_truncate_guard ON {table}")
    for table in ("cluster_snapshots", "cluster_members", "feature_sets", "scores", "alerts"):
        op.execute(f"DROP TRIGGER {table}_run_guard ON {table}")
    op.execute("DROP TRIGGER live_head_complete ON live_run_heads")
    op.execute("DROP TRIGGER runs_completion ON runs")
    op.execute("DROP TRIGGER alerts_immutable_row ON alerts")
    op.execute("DROP TRIGGER alerts_immutable_truncate ON alerts")
    for name in (
        "firewatch_guard_truncate",
        "firewatch_guard_output",
        "firewatch_guard_live_head",
        "firewatch_guard_completion",
        "firewatch_immutable_alert",
    ):
        op.execute(f"DROP FUNCTION {name}()")

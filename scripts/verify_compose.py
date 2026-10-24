"""Docker-only isolated stack acceptance; never reads native/app credentials or provider data."""

import argparse
import json
import socket
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.compose_stack import ROOT, Stack, init_environment


def port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def http(port_number: int, path: str) -> object:
    for attempt in range(20):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port_number}{path}", timeout=4
            ) as response:
                assert response.status == 200
                return json.load(response)
        except (OSError, urllib.error.URLError):
            if attempt == 19:
                raise RuntimeError("Local HTTP service did not become ready") from None
            time.sleep(0.5)
    raise RuntimeError("Local HTTP probe did not run")


CREATE = """
import json, os
from pathlib import Path
from uuid import UUID
from sqlalchemy import select, text
from firewatch.config import get_settings
from firewatch.db.session import Database
from firewatch.db.models import Job, Region
from firewatch.sources.firms_normalize import digest
c=get_settings()
assert os.getuid()==10001 and c.chain_private_key is None and c.firms_map_key is None
d=Database(c)
with d.transaction() as s:
    role=s.execute(text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                        "FROM pg_roles WHERE rolname=current_user")).one()
    assert not any(role)
    assert s.scalar(text("SELECT version_num FROM alembic_version"))=="0003"
    assert s.scalar(text("SELECT count(*) FROM detections"))==0
    regions={r.name:str(r.id) for r in s.scalars(select(Region))}
    assert len(regions)==2
    payload={"schema_version":"compose-durability-probe-v1"}
    s.add(Job(id=UUID(PROBE),type="anchor",payload=payload,payload_hash=digest(payload),idempotency_key="compose-"+PROBE,status="queued",attempts=0))
(c.raw_data_dir/"compose-probe.txt").write_text(PROBE)
(c.model_dir/"compose-probe.txt").write_text(PROBE)
print(json.dumps(regions))
d.close()
"""

READ_ONLY = """
import errno, os
from firewatch.config import get_settings
c=get_settings()
assert os.getuid()==10001 and c.chain_private_key is None and c.firms_map_key is None
for root in (c.raw_data_dir,c.model_dir,c.replay_bundle_dir):
    try:
        (root/"api-must-not-write.txt").write_text("forbidden")
    except OSError as e:
        assert e.errno in (errno.EROFS,errno.EACCES)
    else:
        raise AssertionError("API storage unexpectedly writable")
print("read-only")
"""

PERSISTED = """
import json
from uuid import UUID
from sqlalchemy import select
from firewatch.config import get_settings
from firewatch.db.session import Database
from firewatch.db.models import Job, Region
c=get_settings(); d=Database(c)
assert (c.raw_data_dir/"compose-probe.txt").read_text()==PROBE
assert (c.model_dir/"compose-probe.txt").read_text()==PROBE
with d.session() as s:
    job=s.get(Job,UUID(PROBE))
    assert job.status=="queued" and job.attempts==0
    print(json.dumps({r.name:str(r.id) for r in s.scalars(select(Region))}))
d.close()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--image-tag", default="local")
    parser.add_argument("--optional-services", action="store_true")
    args = parser.parse_args()
    identifier = uuid4().hex
    project = "fwaccept-" + identifier[:12]
    result: dict[str, object] = {
        "schema_version": "compose-acceptance-v1",
        "passed": False,
        "executed_at": datetime.now(UTC).isoformat(),
        "provider_calls": False,
        "synthetic_observations_loaded": False,
        "application_env_modified": False,
        "application_database_used": False,
    }
    stack = None
    stage = "preflight"
    try:
        with tempfile.TemporaryDirectory(prefix="fw-compose-") as directory:
            env = Path(directory) / "stack.env"
            init_environment(env)
            api_port, proxy_port = port(), port()
            with env.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"\nCOMPOSE_API_PORT={api_port}\nCOMPOSE_HTTP_PORT={proxy_port}\n"
                    f"FIREWATCH_IMAGE_TAG={args.image_tag}\n"
                )
            stack = Stack(env, project)
            try:
                stage = "initial_start"
                stack.up(build=not args.no_build)
                assert http(api_port, "/health/live") == {"status": "ok"}
                http(api_port, "/health/ready")
                stage = "storage_and_role"
                # Operator has narrowly scoped write mounts; API/worker do not write model releases.
                create = f"PROBE={identifier!r}\n" + CREATE
                before = json.loads(
                    stack.run(
                        "run",
                        "--rm",
                        "--no-deps",
                        "--entrypoint",
                        "python",
                        "operator",
                        "-c",
                        create,
                    ).strip()
                )
                assert (
                    stack.run("exec", "-T", "api", "python", "-c", READ_ONLY).strip() == "read-only"
                )
                stage = "recreate_persistence"
                stack.run("down")  # Retain this isolated project's volumes.
                stack.up()
                http(api_port, "/health/ready")
                after = json.loads(
                    stack.run(
                        "exec",
                        "-T",
                        "worker",
                        "python",
                        "-c",
                        f"PROBE={identifier!r}\n" + PERSISTED,
                    ).strip()
                )
                assert before == after
                result.update(
                    core_start=True,
                    database_role_unprivileged=True,
                    api_artifact_mounts_read_only=True,
                    raw_model_job_region_persistence=True,
                )
                if args.optional_services:
                    stage = "optional_services"
                    stack.run(
                        "--profile", "chain", "up", "-d", "--wait", "--wait-timeout", "120", "anvil"
                    )
                    assert (
                        stack.run(
                            "exec",
                            "-T",
                            "anvil",
                            "cast",
                            "chain-id",
                            "--rpc-url",
                            "http://127.0.0.1:8545",
                        ).strip()
                        == "31337"
                    )
                    assert (
                        json.loads(
                            stack.run(
                                "exec",
                                "-T",
                                "anvil",
                                "cast",
                                "rpc",
                                "eth_accounts",
                                "--rpc-url",
                                "http://127.0.0.1:8545",
                            )
                        )
                        == []
                    )
                    stack.run("--profile", "proxy", "up", "-d", "--wait", "caddy")
                    assert http(proxy_port, "/health/live") == {"status": "ok"}
                    result.update(optional_anvil=True, optional_http_proxy=True)
                result["passed"] = True
            finally:
                # Delete only volumes belonging to this generated, verified acceptance project.
                assert stack.project == project and project.startswith("fwaccept-")
                stack.run(
                    "--profile",
                    "chain",
                    "--profile",
                    "proxy",
                    "--profile",
                    "tools",
                    "down",
                    "--volumes",
                    "--remove-orphans",
                )
                result["isolated_project_removed"] = True
    except Exception:
        result["passed"] = False
        result["failed_stage"] = stage
        print("Compose acceptance failed; no raw service configuration/logs printed")
    output = ROOT / "artifacts/phase-5.6-compose.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

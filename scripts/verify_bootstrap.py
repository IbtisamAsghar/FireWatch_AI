"""Verify local PostgreSQL transactions and real HTTP bootstrap without exposing DSNs."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from firewatch.config import get_settings
from firewatch.db.session import Database


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    database = Database(settings)
    try:
        with database.session() as session:
            role = session.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            if any(role):
                raise RuntimeError("Application role must not have administrative privileges")
        result = database.check_connection()
        if result["timezone"] != "UTC" or not result["version"].startswith("16."):
            raise RuntimeError("Expected PostgreSQL 16 and UTC sessions")
    finally:
        database.close()
    environment = dict(os.environ)
    environment["FIREWATCH_TEST_DATABASE_URL"] = (
        make_url(settings.database_url.get_secret_value())
        .set(database="firewatch_test")
        .render_as_string(hide_password=False)
    )
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, env=environment, check=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "firewatch.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health/live", timeout=1
                    ) as response:
                        if response.status == 200 and response.read() == b'{"status":"ok"}':
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("API did not become live within 20 seconds")
                time.sleep(0.2)
            for route in (
                "/health/ready",
                "/api/v1/regions",
                "/api/v1/status",
                "/docs",
                "/openapi.json",
            ):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{route}", timeout=3
                ) as response:
                    assert response.status == 200
            print(
                "HTTP smoke passed: liveness, readiness, regions, status, Swagger, OpenAPI; "
                "app role is unprivileged."
            )
        finally:
            process.terminate()
            process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

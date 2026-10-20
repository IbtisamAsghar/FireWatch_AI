"""Real HTTP acceptance with graceful cleanup: python -m scripts.verify_demo."""

import json
import socket
import time
import urllib.error
import urllib.request
from threading import Thread

import uvicorn
from sqlalchemy import create_engine, text

from scripts.demo_api import configured_demo, create_demo_app
from scripts.demo_data import IDS, read_requests


def main() -> None:
    settings = configured_demo()
    app = create_demo_app(settings)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = Thread(target=server.run, daemon=True)
    schema = None
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Demo API failed to become ready")
            time.sleep(0.1)
        with app.state.demo_engine.connect() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
        for operation, path in read_requests().items():
            headers = {}
            if operation == "get_job":
                headers["Authorization"] = "Bearer " + settings.admin_api_token.get_secret_value()
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
                json.load(response)
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/jobs/{IDS['job']}", headers=headers
            )
            try:
                urllib.request.urlopen(request, timeout=5).close()
                raise RuntimeError("Protected demo job unexpectedly allowed invalid credentials")
            except urllib.error.HTTPError as response:
                assert response.code == 401
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=5) as response:
            assert response.status == 200
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=5) as response:
            assert "SYNTHETIC" in json.load(response)["info"]["title"]
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():
            raise RuntimeError("Demo API did not stop gracefully")
    control = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with control.connect() as connection:
            assert not connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                {"schema": schema},
            )
    finally:
        control.dispose()
    print("Demo HTTP acceptance passed: all 15 reads, Swagger, authentication and schema cleanup.")


if __name__ == "__main__":
    main()

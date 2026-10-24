"""Local Compose release operator; never loads or overwrites the native .env."""

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def init_environment(path: Path) -> bool:
    if path.exists():
        return False
    template = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    for placeholder in (
        "REPLACE_WITH_GENERATED_BOOTSTRAP_PASSWORD",
        "REPLACE_WITH_GENERATED_APP_PASSWORD",
        "REPLACE_WITH_GENERATED_OPERATOR_TOKEN",
    ):
        template = template.replace(placeholder, secrets.token_urlsafe(48))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(template)
    if sys.platform == "win32":
        from scripts.native_postgres import protect

        protect(path)
    return True


def environment(path: Path) -> dict[str, str]:
    values = dotenv_values(path, interpolate=False)
    for key in ("POSTGRES_PASSWORD", "APP_DATABASE_PASSWORD", "ADMIN_API_TOKEN"):
        value = values.get(key) or ""
        if value.startswith("REPLACE_") or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value):
            raise ValueError("Compose secrets must be generated URL-safe values")
    result = dict(os.environ)
    result.update({key: value for key, value in values.items() if value is not None})
    return result


class Stack:
    def __init__(self, path: Path, project: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", project):
            raise ValueError("Invalid Compose project name")
        self.environment = environment(path)
        self.project = project
        self.prefix = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(path.resolve()),
            "--file",
            str(ROOT / "compose.yaml"),
        ]

    def run(self, *args: str, timeout: int = 180) -> str:
        result = subprocess.run(
            [*self.prefix, *args],
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode:
            # Compose failures can echo a resolved environment; keep raw output private.
            raise RuntimeError("Compose command failed; inspect service state locally")
        return result.stdout

    def preflight(self) -> None:
        if shutil.which("docker") is None:
            raise RuntimeError("Install/start Docker with Linux containers and Compose")
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode or result.stdout.strip() != "linux":
            raise RuntimeError("A running Linux Docker engine is required")
        self.run("config", "--quiet")

    def up(self, *, build: bool = False) -> None:
        self.preflight()
        if build:
            print("Building the shared API/worker image", flush=True)
            self.run("build", timeout=1800)
        # Quiesce existing application writers before the explicit release migration.
        self.run("stop", "api", "worker", "anchor-worker", "caddy")
        self.run("up", "--detach", "--wait", "--wait-timeout", "180", "db")
        self.run("run", "--rm", "--no-deps", "storage-init")
        print("Applying release migrations and region configuration", flush=True)
        self.run("run", "--rm", "--no-deps", "migrate")
        self.run("run", "--rm", "--no-deps", "seed")
        self.run("up", "--detach", "--wait", "--wait-timeout", "180", "api", "worker", timeout=240)
        print("API and worker are healthy; no synthetic observations were loaded", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.compose")
    parser.add_argument("--project-name", default="firewatch")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    start = commands.add_parser("up")
    start.add_argument("--build", action="store_true")
    commands.add_parser("status")
    commands.add_parser("down")
    args = parser.parse_args()
    try:
        if args.command == "init":
            created = init_environment(args.env_file)
            print(
                "Compose environment created"
                if created
                else "Existing Compose environment preserved"
            )
            return 0
        stack = Stack(args.env_file, args.project_name)
        if args.command == "up":
            stack.up(build=args.build)
        else:
            stack.preflight()
            if args.command == "status":
                print(stack.run("ps"))
            else:
                stack.run("down")  # Never delete persistent volumes.
                print("Stack stopped; persistent volumes retained")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        print(
            "Stack operation stopped. Check Docker/Compose, the private Compose env file, "
            "service status and documented recovery steps.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

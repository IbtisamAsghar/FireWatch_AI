"""Verify FIRMS artifacts and export Module 2 handoff without HTTP or database writes."""

import argparse
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

from firewatch.config import get_settings
from firewatch.sources.firms_handoff import HandoffError, export_firms_handoff, scan_secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", required=True, type=Path)
    parser.add_argument("--refresh", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    settings = get_settings()
    database = settings.database_url.get_secret_value()
    secrets = [
        database,
        make_url(database).password or "",
        settings.admin_api_token.get_secret_value(),
    ]
    if settings.firms_map_key:
        secrets.append(settings.firms_map_key.get_secret_value())
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
        )
        files = [Path(name) for name in tracked.stdout.decode().split("\0") if name]
        scanned = scan_secrets((p for p in files if p.is_file()), secrets)
        report = export_firms_handoff(args.archive, args.refresh, args.output, secrets=secrets)
    except (HandoffError, OSError, subprocess.SubprocessError):
        parser.exit(1, "FIRMS acceptance failed; check inputs and secret hygiene.\n")
    print(
        f"Verified {len(report['datasets'])} archives, {len(report['refreshes'])} refreshes; "
        f"{scanned} repository files scanned. Handoff exported; evaluation readiness is false."
    )


if __name__ == "__main__":
    main()

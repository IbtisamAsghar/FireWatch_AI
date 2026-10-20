"""Dependency-free repository checks; run with Python 3.12 from any directory."""
import ast
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "LICENSE", "CONTRIBUTING.md", "FireWatchImplementationPlan.md",
    ".env.example", ".gitignore", ".dockerignore", ".editorconfig", ".gitattributes",
    ".python-version", ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/task.yml",
    ".github/pull_request_template.md", "docs/architecture-decisions.md",
    "docs/github-setup.md", "docs/phase-1.1-checklist.md", "docs/contracts.md",
    "docs/data-dictionary.md", "docs/model-card.md", "docs/runbook.md",
    "docs/frontend-handoff.md", "configs/regions.yaml", "configs/features-v1.yaml",
    "configs/model-v1.yaml", "migrations/README.md", "contracts/README.md",
    "deploy/README.md", "data/sample/README.md", "models/README.md",
    "tests/unit/README.md", "tests/integration/README.md",
    "tests/contract/README.md", "tests/e2e/README.md",
)

def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], text=True, capture_output=True, check=False
    )

def main():
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append("Use Python 3.12, matching the project runtime and CI.")
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing required scaffold file: {name}")
    python_files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"Invalid Python: {path.relative_to(ROOT)}: {exc}")
    documents = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in documents:
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", path.read_text(encoding="utf-8-sig")):
            if re.match(r"[a-zA-Z][\w+.-]*:", target) or target.startswith("#"):
                continue
            target_path = unquote(target.split("#", 1)[0])
            if target_path and not (path.parent / target_path).exists():
                errors.append(f"Broken local link in {path.relative_to(ROOT)}: {target}")
    template = ROOT / ".env.example"
    values = {}
    for line in template.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            errors.append(f"Invalid or duplicate environment key: {key}")
        values[key] = value
    for key in ("POSTGRES_PASSWORD", "FIRMS_MAP_KEY", "ADMIN_API_TOKEN"):
        if not values.get(key, "").startswith("REPLACE_"):
            errors.append(f"Environment template needs a placeholder for {key}")
    if values.get("CHAIN_ENABLED") != "false" or values.get("CHAIN_PRIVATE_KEY") != "":
        errors.append("Optional chain must be disabled and its private key empty.")
    if "REPLACE_ME" not in values.get("DATABASE_URL", ""):
        errors.append("Database URL must use a placeholder password.")
    for name in (
        ".env", ".env.production", "secrets/token.txt", "wallets/demo.json",
        "keystore/demo.json", "private.key", "server.pem", ".venv/file.txt",
        "data/raw/fire.csv", "data/archive/fire.parquet", "models/private.joblib",
        "models/private/manifest.json", "backups/db.dump", ".chain/state.json",
    ):
        result = git("check-ignore", "--no-index", "-q", name)
        if result.returncode != 0:
            errors.append(f"Git does not exclude sensitive/generated path: {name}")
    for name in (".env.example", "models/README.md", "data/sample/README.md", "requirements.lock"):
        result = git("check-ignore", "--no-index", "-q", name)
        if result.returncode != 1:
            errors.append(f"Git unexpectedly ignores required project path: {name}")
    tracked = git("ls-files", "-z")
    if tracked.returncode:
        errors.append("Cannot inspect tracked files with Git.")
    for name in tracked.stdout.split("\0"):
        if not name or name == ".env.example":
            continue
        parts = Path(name).parts
        if (Path(name).name == ".env" or Path(name).name.startswith(".env.")
                or any(part in {"wallets", "keystore", "secrets", ".venv"} for part in parts)
                or name.startswith(("data/raw/", "data/archive/", "backups/", ".chain/"))
                or (name.startswith("models/") and name != "models/README.md")
                or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".joblib", ".pkl", ".pickle"}):
            errors.append(f"Sensitive/generated file is tracked: {name}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Scaffold checks passed: {len(REQUIRED)} required files, {len(python_files)} Python files, links, template and Git exclusions.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

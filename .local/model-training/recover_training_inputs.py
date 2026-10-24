"""Recover the exact public NASA source bytes without fabricating observations."""
import hashlib
import json
import zipfile
from pathlib import Path

import httpx

root = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').is_file())
out = root / 'data' / 'recovered-archives'
out.mkdir(parents=True, exist_ok=True)
for year in (2022, 2024):
    manifest = json.loads((root / f'docs/manifests/season-{year}-archive.json').read_text())
    target = out / f'season-{year}.csv'
    if target.exists():
        assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest['sha256']
        print(year, 'existing exact archive verified', flush=True)
        continue
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        response = client.get(manifest['source_url'])
        response.raise_for_status()
    archive = out / f'season-{year}.zip'
    archive.write_bytes(response.content)
    with zipfile.ZipFile(archive) as bundle:
        members = [item for item in bundle.infolist() if item.filename.lower().endswith('.csv')]
        assert len(members) == 1, 'Expected one original CSV'
        assert members[0].file_size < 50 * 1024 * 1024
        data = bundle.read(members[0])
    assert hashlib.sha256(data).hexdigest() == manifest['sha256'], 'Original CSV digest differs'
    target.write_bytes(data)
    print(year, 'NASA CSV recovered and matches original SHA-256;', len(data), 'bytes', flush=True)

"""Regenerate the Nix wheel manifest from uv.lock on x86_64 Linux / Python 3.12.

Run: uv run python nix/update-wheels.py
"""
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

root = Path(__file__).resolve().parents[1]
if sys.version_info[:2] != (3, 12) or sys.platform != 'linux':
    raise SystemExit('Use Python 3.12 on x86_64 Linux to select package wheels')
import platform
if platform.machine() != 'x86_64':
    raise SystemExit('This manifest targets x86_64 Linux only')
ranks = {str(tag): index for index, tag in enumerate(sys_tags())}
selected = []
for package in tomllib.loads((root / 'uv.lock').read_text())['package']:
    if package['name'] in {'findmatch', 'colorama'}:
        continue
    candidates = []
    for wheel in package.get('wheels', []):
        name = urlparse(wheel['url']).path.rsplit('/', 1)[1]
        tags = parse_wheel_filename(name)[3]
        rank = min((ranks.get(str(tag), 1_000_000) for tag in tags), default=1_000_000)
        if rank < 1_000_000:
            candidates.append((rank, wheel, name))
    if not candidates:
        raise SystemExit(f"No compatible locked wheel: {package['name']}")
    _, wheel, name = min(candidates, key=lambda entry: entry[0])
    selected.append(dict(name=name, url=wheel['url'], sha256=wheel['hash'].split(':')[1]))
(root / 'nix/wheels-x86_64-linux.json').write_text(json.dumps(selected, indent=2) + '\n')
print(f'Selected {len(selected)} hash-pinned wheels')

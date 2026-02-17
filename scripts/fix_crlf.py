#!/usr/bin/env python3
"""
Simple helper to normalise line endings to LF for shell scripts under the repo.
"""
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
changed = []
for p in repo.rglob('*.sh'):
    b = p.read_bytes()
    if b.find(b'\r\n') != -1:
        p.write_bytes(b.replace(b'\r\n', b'\n'))
        changed.append(str(p.relative_to(repo)))
print('normalized:', changed)

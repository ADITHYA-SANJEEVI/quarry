"""Check the exact Git index for prohibited release artifacts; prints no contents."""
from pathlib import Path
import json
import re
import subprocess

root = Path(__file__).resolve().parents[1]
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
bad = []
for name in filter(None, files):
    p = Path(name)
    if (name.startswith('.github/workflows/') or
        (p.name.startswith('.env') and p.name != '.env.example') or
        p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.jsonl', '.zip', '.7z', '.pem', '.key', '.log', '.pyc', '.exe'} or
        any(x in p.parts for x in ('node_modules', '.venv', '__pycache__', '.pytest_cache', 'artifacts'))):
        bad.append(name)
    raw = (root / name).read_bytes()
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', raw):
        bad.append(name + ': private-key material')
print(json.dumps({'tracked_files': len(list(filter(None, files))), 'prohibited_paths_or_private_keys': bad}))
raise SystemExit(bool(bad))

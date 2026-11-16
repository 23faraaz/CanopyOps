"""Generate local-only Compose secrets. Existing values are never replaced."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / '.secrets'
folder.mkdir(mode=0o700, exist_ok=True)
if os.name == 'posix':
    folder.chmod(0o700)  # Protect traversal on the host; only selected files are mounted.
for name in ['pg_admin','pg_migrator','pg_reader','pg_writer']:
    path = folder / name
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        continue
    if hasattr(os, 'fchmod'):
        os.fchmod(descriptor, 0o644)  # File-based Compose secrets must be readable by UID 10001.
    with os.fdopen(descriptor,'w',newline='\n') as output:
        output.write(secrets.token_hex(32)+'\n')
print('Local secret files ready; values are not printed or committed.')

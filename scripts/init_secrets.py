"""Validate external password-file presence without reading or generating passwords."""
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = Path(os.environ.get("CANOPY_SECRETS_DIR", str(root.parent / "CanopyOps-secrets"))).resolve()
if folder == root or root in folder.parents:
    raise SystemExit("Secret directory must be outside the repository")
names = ["pg_admin", "pg_migrator", "pg_reader", "pg_writer"]
missing = [name for name in names if not (folder / name).is_file()]
if missing:
    raise SystemExit("Missing external password files: " + ", ".join(missing) + "; restore the existing files, never regenerate them")
print("External password files present; contents were not read")

"""Cheap daily expiry tracking; full image-scope validation stays in checks.py."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from image_policy import utc
from verify_docker import BASE, ROOT

VENDOR_PROBE = r"""
import json,re,subprocess,sys
subprocess.run(['apk','update'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
policy=subprocess.check_output(['apk','policy','sqlite-libs'],text=True,stderr=subprocess.PIPE)
versions=sorted(set(re.findall(r'^\s+([0-9][A-Za-z0-9_.+-]*):',policy,re.MULTILINE)))
if not versions: raise RuntimeError('No SQLite candidate metadata found')
newer=[v for v in versions if subprocess.check_output(['apk','version','--test',v,sys.argv[1]],text=True).strip()=='>']
print(json.dumps({'repository':'Alpine stable repositories configured in the pinned Python base',
                  'installed_reviewed_version':sys.argv[1],'available_versions':versions,
                  'newer_versions':newer,'advisory_closure_verified':False}))
"""


def assess(policy, now=None):
    now = now or datetime.now(timezone.utc)
    if not policy.get("approved_by"):
        raise ValueError("No named risk acceptance is active")
    approved = utc(policy["approved_at"])
    expires = utc(policy["expires_at"])
    if approved > now or expires <= approved or expires - approved > timedelta(days=7):
        raise ValueError("Invalid risk acceptance dates")
    hours = (expires - now).total_seconds() / 3600
    return {
        "observed_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "hours_remaining": round(hours, 2),
        "status": "expired" if hours <= 0 else "warning" if hours <= 72 else "active",
        "approved_by": policy["approved_by"],
        "cves": policy["cves"],
        "scope": "local synthetic-data build only; no AWS/production approval",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-vendor", action="store_true")
    args = parser.parse_args()
    policy = json.loads((ROOT / "security/sqlite-risk-acceptance.json").read_text())
    result = assess(policy)
    print(json.dumps(result, indent=2), flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as output:
            output.write(
                f"SQLite risk acceptance: **{result['status']}**. Expires **{result['expires_at']}**. "
                f"{result['hours_remaining']} hours remain. Local synthetic-data scope only.\n"
            )
    if result["status"] == "expired":
        raise SystemExit("SQLite acceptance expired; no renewal was performed")
    if result["status"] == "warning":
        print(
            "::warning::SQLite exceptions expire within 72 hours; review vendor fixes before expiry",
            flush=True,
        )
    if args.probe_vendor:
        version = policy["scope"]["sqlite_package"].removeprefix("sqlite-libs=")
        vendor = json.loads(
            subprocess.check_output(
                ["docker", "run", "--rm", BASE, "python", "-c", VENDOR_PROBE, version],
                text=True,
                cwd=ROOT,
            )
        )
        print("Vendor recheck: " + json.dumps(vendor), flush=True)
        if vendor["newer_versions"]:
            print(
                "::warning::A newer SQLite package is available; review advisories and test it. Acceptance was not changed.",
                flush=True,
            )
    print("Deadline check completed; no policy or package was changed", flush=True)


if __name__ == "__main__":
    main()

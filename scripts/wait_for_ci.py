"""Wait for the latest main-push CI run for this exact SHA, using a read-only GitHub token."""

import argparse
import json
import re
import subprocess
import time


def wait(repository, commit, timeout=600):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(
        r"[a-f0-9]{40}", commit
    ):
        raise ValueError("Invalid repository or full commit SHA")
    deadline = time.monotonic() + timeout
    path = f"repos/{repository}/actions/workflows/ci.yml/runs?head_sha={commit}&event=push&branch=main&per_page=1"
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["gh", "api", path], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode:
            raise RuntimeError(
                "Cannot read required CI status; no publishing credentials requested"
            )
        runs = json.loads(result.stdout)["workflow_runs"]
        if runs and runs[0]["status"] == "completed":
            if runs[0]["head_sha"] != commit or runs[0]["conclusion"] != "success":
                raise RuntimeError("Latest required CI run did not pass for this exact commit")
            print("Latest main-push CI passed for the release commit")
            return
        print("Waiting for required CI; AWS credentials have not been requested", flush=True)
        time.sleep(10)
    raise RuntimeError("Required CI did not finish before the publishing wait deadline")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    wait(args.repository, args.commit)

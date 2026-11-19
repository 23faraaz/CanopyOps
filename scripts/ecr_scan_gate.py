"""Fail closed on native ECR scan results for one exact published digest."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL", "UNDEFINED"}


def evaluate(report, account, repository, digest, now=None):
    now = now or datetime.now(timezone.utc)
    if (
        report.get("registryId") != account
        or report.get("repositoryName") != repository
        or report.get("imageId", {}).get("imageDigest") != digest
    ):
        raise ValueError("Native ECR report identity does not match the published image")
    if report.get("imageScanStatus", {}).get("status") != "COMPLETE":
        raise ValueError("Native ECR scan is not COMPLETE")
    findings = report.get("imageScanFindings")
    if not isinstance(findings, dict):
        raise ValueError("Native ECR findings are missing")
    counts = findings.get("findingSeverityCounts")
    entries = findings.get("findings")
    if not isinstance(counts, dict) or not isinstance(entries, list):
        raise ValueError("Native ECR finding counts or entries are missing")
    if any(k not in SEVERITIES or type(v) is not int or v < 0 for k, v in counts.items()):
        raise ValueError("Native ECR severity counts are invalid")
    try:
        completed = datetime.fromisoformat(findings["imageScanCompletedAt"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Native ECR scan timestamp is missing or invalid") from error
    if completed.tzinfo is None or not -timedelta(minutes=5) <= now - completed <= timedelta(
        hours=24
    ):
        raise ValueError("Native ECR scan timestamp is invalid or older than 24 hours")
    if any(not isinstance(v, dict) or v.get("severity") not in SEVERITIES for v in entries):
        raise ValueError("Native ECR finding severity is invalid")
    if (
        counts.get("HIGH", 0)
        or counts.get("CRITICAL", 0)
        or any(v["severity"] in {"HIGH", "CRITICAL"} for v in entries)
    ):
        raise ValueError("Native ECR HIGH/CRITICAL vulnerability gate failed")
    return {"status": "COMPLETE", "completed_at": completed.isoformat(), "counts": counts}


def check(aws, account, repository, digest, report_path):
    """AWS CLI waiter polls every five seconds, failing after 60 unsuccessful checks."""
    arguments = (
        "--registry-id",
        account,
        "--repository-name",
        repository,
        "--image-id",
        "imageDigest=" + digest,
    )
    wait_failed = False
    try:
        aws("ecr", "wait", "image-scan-complete", *arguments)
    except RuntimeError:
        wait_failed = True
    # Retain the exact report even when the waiter or vulnerability gate fails.
    report = json.loads(aws("ecr", "describe-image-scan-findings", *arguments, "--output", "json"))
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    if wait_failed:
        raise RuntimeError("Native ECR scan waiter failed; release receipt withheld")
    summary = evaluate(report, account, repository, digest)
    print("Native ECR gate passed: " + json.dumps(summary), flush=True)
    return summary

"""Fail closed on native ECR scan results for one exact published digest."""

from datetime import datetime, timedelta, timezone
import json
import re
import time
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


class AwsApiError(RuntimeError):
    """Expose an AWS error code, never request credentials or raw error output."""

    def __init__(self, code):
        self.code = code if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", code) else "UnknownAwsError"
        super().__init__("AWS request failed: " + self.code)


def check(aws, account, repository, digest, report_path, attempts=60, sleep=time.sleep):
    """Bounded polling tolerates only the documented not-yet-visible scan race."""
    if type(attempts) is not int or not 1 <= attempts <= 60:
        raise ValueError("Native scan polling must remain bounded")
    arguments = (
        "--registry-id",
        account,
        "--repository-name",
        repository,
        "--image-id",
        "imageDigest=" + digest,
        "--output",
        "json",
    )
    report = None
    failure = None
    for attempt in range(attempts):
        try:
            report = json.loads(aws("ecr", "describe-image-scan-findings", *arguments))
        except AwsApiError as error:
            report = {
                "error_code": error.code,
                "registryId": account,
                "repositoryName": repository,
                "imageId": {"imageDigest": digest},
            }
            if error.code not in {"ScanNotFoundException", "ImageNotFoundException"}:
                failure = error
                break
        except (RuntimeError, ValueError):
            report = {"error_code": "ScanRequestOrReportFailed"}
            failure = RuntimeError(
                "Native ECR scan request/report failed; release receipt withheld"
            )
            break
        else:
            if not isinstance(report, dict):
                failure = ValueError("Native ECR scan report must be an object")
                break
            status = report.get("imageScanStatus", {}).get("status")
            if status == "COMPLETE":
                break
            if status not in {"IN_PROGRESS", "PENDING"}:
                failure = RuntimeError("Native ECR scan did not complete: " + str(status))
                break
        if attempt + 1 < attempts:
            sleep(5)
    else:
        failure = RuntimeError("Native ECR scan did not complete within the bounded polling window")
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    if failure:
        raise failure
    summary = evaluate(report, account, repository, digest)
    print("Native ECR gate passed: " + json.dumps(summary), flush=True)
    return summary

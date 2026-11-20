"""Strict public deployment metadata, release provenance and saved-plan intent checks.

No credential/environment access. Only the explicitly supplied public JSON file is read.
"""

from ipaddress import IPv4Network
import json
from pathlib import Path
import re

COMPONENTS = {"api", "worker", "analytics"}
BASE_FIELDS = {
    "project_name",
    "environment",
    "aws_region",
    "vpc_cidr",
    "public_subnet_cidrs",
    "private_app_subnet_cidrs",
    "isolated_data_subnet_cidrs",
    "availability_zones",
    "application",
}
APP_FIELDS = {
    "permissions_boundary_arn",
    "domain_name",
    "hosted_zone_id",
    "certificate_arn",
    "postgres_version",
    "redis_version",
    "final_snapshot_identifier",
    "redis_user_ids",
    "repository_arns",
    "image_uris",
    "activate_services",
    "pause_ingestion",
    "teardown_prepared",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON keys are forbidden")
        result[key] = value
    return result


def validate_document(document, account):
    require(matches(r"[0-9]{12}", account), "Invalid deployment account")
    require(
        isinstance(document, dict) and set(document) == {"schema", "release", "inputs"},
        "Unexpected deployment document fields",
    )
    require(
        type(document["schema"]) is int and document["schema"] == 1,
        "Unsupported deployment document schema",
    )
    inputs = document["inputs"]
    require(
        isinstance(inputs, dict) and set(inputs) == BASE_FIELDS,
        "Unexpected deployment input fields; credentials are forbidden",
    )
    require(
        (inputs["project_name"], inputs["environment"], inputs["aws_region"])
        == ("canopyops", "demo", "eu-west-2"),
        "Deployment must target the London demo",
    )
    try:
        vpc = IPv4Network(inputs["vpc_cidr"], strict=True)
        subnets = []
        for field in [
            "public_subnet_cidrs",
            "private_app_subnet_cidrs",
            "isolated_data_subnet_cidrs",
        ]:
            require(
                isinstance(inputs[field], list) and len(inputs[field]) == 2,
                "Provide two subnets per tier",
            )
            subnets.extend(IPv4Network(cidr, strict=True) for cidr in inputs[field])
        require(
            vpc.is_private and all(net.subnet_of(vpc) for net in subnets),
            "Subnets must be inside the private VPC",
        )
        require(
            all(
                not left.overlaps(right)
                for i, left in enumerate(subnets)
                for right in subnets[i + 1 :]
            ),
            "Subnets must not overlap",
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid or overlapping IPv4 deployment networks") from error
    azs = inputs["availability_zones"]
    require(
        isinstance(azs, list)
        and len(azs) == 2
        and all(matches(r"eu-west-2[a-z]", az) for az in azs)
        and len(set(azs)) == 2,
        "Use two distinct London availability zones",
    )
    app = inputs["application"]
    if app is None:
        require(
            document["release"] is None,
            "Network-only configuration must not claim an application release",
        )
        return document
    require(
        isinstance(app, dict) and set(app) == APP_FIELDS,
        "Unexpected application fields; secrets belong outside deployment inputs",
    )
    require(
        app["permissions_boundary_arn"]
        == f"arn:aws:iam::{account}:policy/canopyops-demo-runtime-boundary",
        "Wrong runtime boundary",
    )
    require(
        matches(
            r"arn:aws:acm:eu-west-2:" + account + r":certificate/[a-f0-9-]{36}",
            app["certificate_arn"],
        ),
        "Use the retained London certificate from this account",
    )
    require(
        matches(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:[.][a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
            app["domain_name"],
        ),
        "Use an owned lowercase application hostname",
    )
    require(matches(r"Z[A-Z0-9]+", app["hosted_zone_id"]), "Invalid public zone ID")
    require(
        matches(r"17[.][0-9]+", app["postgres_version"]),
        "Use the rehearsed PostgreSQL major version",
    )
    require(matches(r"7[.][0-9]+", app["redis_version"]), "Use reviewed Redis OSS 7 metadata")
    require(
        matches(r"canopyops-demo-[a-z0-9]+(?:-[a-z0-9]+)*", app["final_snapshot_identifier"])
        and len(app["final_snapshot_identifier"]) <= 63,
        "Use a reviewed uniquely named final snapshot",
    )
    for flag in ["activate_services", "pause_ingestion", "teardown_prepared"]:
        require(type(app[flag]) is bool, "Application control flags must be booleans")
    users = app["redis_user_ids"]
    require(
        isinstance(users, list)
        and all(matches(r"canopyops-demo-[a-z0-9-]+", name) for name in users),
        "Use provisioned project Redis user IDs only",
    )
    require(
        users == sorted(set(users)) and (not users or len(users) >= 2),
        "Redis IDs must be empty or a sorted distinct reviewed user set",
    )
    require(
        not app["activate_services"] or len(users) >= 2,
        "Keep tasks stopped until Redis identities and credentials are provisioned",
    )
    require(
        not app["teardown_prepared"] or (not app["activate_services"] and app["pause_ingestion"]),
        "Teardown requires stopped services and fenced writes",
    )
    for field in ["repository_arns", "image_uris"]:
        require(
            isinstance(app[field], dict) and set(app[field]) == COMPONENTS,
            "Provide exactly the three application components",
        )
    for component in COMPONENTS:
        require(
            app["repository_arns"][component]
            == f"arn:aws:ecr:eu-west-2:{account}:repository/canopyops-demo/{component}",
            "Repository is outside the approved account/namespace",
        )
        require(
            matches(
                f"{account}[.]dkr[.]ecr[.]eu-west-2[.]amazonaws[.]com/canopyops-demo/{component}@sha256:[a-f0-9]{{64}}",
                app["image_uris"][component],
            ),
            "Images must be approved ECR digests, never mutable tags",
        )
    release = document["release"]
    require(
        isinstance(release, dict) and set(release) == {"commit", "run_id", "attempt"},
        "Application images need exact publishing provenance",
    )
    require(
        matches(r"[0-9a-f]{40}", release["commit"])
        and all(matches(r"[1-9][0-9]*", release[key]) for key in ["run_id", "attempt"]),
        "Invalid publishing identity",
    )
    return document


def load_document(path, account):
    path = Path(path)
    require(
        not path.is_symlink() and path.is_file() and path.stat().st_size <= 65536,
        "Use a small regular public deployment document",
    )
    return validate_document(json.loads(path.read_text(), object_pairs_hook=unique_object), account)


def verify_plan_inputs(plan, document):
    variables = plan.get("variables", {})
    require(
        all(
            variables.get(key, {}).get("value") == value
            for key, value in document["inputs"].items()
        ),
        "Saved plan inputs differ from the reviewed deployment document",
    )


def verify_release_run(run, release):
    require(
        str(run.get("id")) == release["run_id"]
        and str(run.get("run_attempt")) == release["attempt"],
        "Publishing run/attempt changed",
    )
    require(
        run.get("repository", {}).get("full_name") == "23faraaz/CanopyOps"
        and run.get("head_repository", {}).get("full_name") == "23faraaz/CanopyOps",
        "Publishing source is not this repository",
    )
    require(
        run.get("head_branch") == "main"
        and run.get("head_sha") == release["commit"]
        and run.get("path", "").split("@")[0] == ".github/workflows/application.yml",
        "Images must come from the selected main publishing workflow",
    )
    require(
        run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed"
        and run.get("conclusion") == "success",
        "Publishing must complete successfully; PR builds do not authorize release",
    )


def verify_release_receipts(records, document):
    require(
        isinstance(records, list)
        and len(records) == 3
        and all(isinstance(row, dict) for row in records),
        "Require three publishing receipts",
    )
    require(
        all(isinstance(row.get("component"), str) for row in records)
        and {row.get("component") for row in records} == COMPONENTS,
        "Missing or duplicate release component",
    )
    release = document["release"]
    tag = f"build-{release['commit']}-{release['run_id']}-{release['attempt']}"
    for row in records:
        require(
            row.get("commit") == release["commit"] and row.get("tag") == tag,
            "Images belong to different publishing identities",
        )
        component = row["component"]
        uri = document["inputs"]["application"]["image_uris"][component]
        require(
            row.get("uri") == uri and row.get("digest") == uri.split("@", 1)[1],
            "Release receipt does not match the reviewed image digest",
        )
        scan = row.get("native_scan", {})
        counts = scan.get("counts")
        require(
            scan.get("status") == "COMPLETE" and isinstance(counts, dict),
            "Native ECR scan evidence is missing or incomplete",
        )
        require(
            all(type(count) is int and count >= 0 for count in counts.values())
            and all(counts.get(level, 0) == 0 for level in ["HIGH", "CRITICAL"]),
            "Published release failed its native vulnerability gate",
        )

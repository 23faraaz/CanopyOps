"""Pure, fail-closed application plan checks. Never reads files or authenticates."""

import json
import re

RESOURCES = {
    "security": {
        "aws_security_group": {"this"},
        "aws_vpc_security_group_ingress_rule": {"alb_public", "connection"},
        "aws_vpc_security_group_egress_rule": {"connection"},
    },
    "connectivity": {
        "aws_vpc_endpoint": {"interface", "s3"},
        "aws_vpc_security_group_egress_rule": {"task_image_layers"},
    },
    "foundation": {
        "aws_ecs_cluster": {"this"},
        "aws_kms_key": {"logs", "outbox"},
        "aws_kms_alias": {"logs"},
        "aws_s3_bucket": {"alb_logs", "outbox"},
        "aws_s3_bucket_public_access_block": {"alb_logs", "outbox"},
        "aws_s3_bucket_ownership_controls": {"alb_logs", "outbox"},
        "aws_s3_bucket_versioning": {"alb_logs", "outbox"},
        "aws_s3_bucket_server_side_encryption_configuration": {"alb_logs", "outbox"},
        "aws_s3_bucket_lifecycle_configuration": {"alb_logs"},
        "aws_s3_bucket_policy": {"alb_logs", "outbox"},
        "aws_secretsmanager_secret": {"runtime"},
        "aws_iam_role": {"task", "execution"},
        "aws_iam_role_policy": {"execution", "queue", "outbox"},
        "aws_elasticache_user_group": {"analytics"},
        "aws_cloudwatch_log_group": {"generator"},
        "aws_ecs_task_definition": {"generator"},
    },
    "database": {
        "aws_db_instance": {"this"},
        "aws_db_subnet_group": {"this"},
        "aws_db_parameter_group": {"this"},
        "aws_cloudwatch_log_group": {"postgres"},
    },
    "cache": {
        "aws_elasticache_replication_group": {"this"},
        "aws_elasticache_subnet_group": {"this"},
    },
    "queue": {
        "aws_sqs_queue": {"readings", "dead_letter"},
        "aws_sqs_queue_policy": {"https_only"},
        "aws_sqs_queue_redrive_allow_policy": {"this"},
    },
    "ingress": {
        "aws_acm_certificate": {"this"},
        "aws_acm_certificate_validation": {"this"},
        "aws_route53_record": {"validation", "application"},
        "aws_lb": {"this"},
        "aws_lb_target_group": {"this"},
        "aws_lb_listener": {"http", "https"},
        "aws_lb_listener_rule": {"metrics_private", "https", "redirect"},
        "aws_wafv2_web_acl": {"this"},
        "aws_wafv2_web_acl_association": {"this"},
        "aws_wafv2_web_acl_logging_configuration": {"this"},
        "aws_cloudwatch_log_group": {"waf"},
    },
    "service": {
        "aws_ecs_task_definition": {"this"},
        "aws_ecs_service": {"this"},
        "aws_cloudwatch_log_group": {"this"},
    },
}
# Only metadata is rendered into the public review. Policies and container JSON
# stay in the private saved plan and must be reviewed against the pinned source.
FIELDS = [
    "name",
    "family",
    "identifier",
    "engine",
    "engine_version",
    "multi_az",
    "publicly_accessible",
    "storage_encrypted",
    "deletion_protection",
    "desired_count",
    "cpu",
    "memory",
    "permissions_boundary",
    "private_dns_enabled",
    "vpc_endpoint_type",
    "from_port",
    "to_port",
    "ip_protocol",
    "cidr_ipv4",
    "cidr_ipv6",
    "zone_id",
    "type",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(resource, application, mode):
    require(isinstance(application, dict), "Plan scope requires explicit application inputs")
    address = resource["address"]
    match = re.fullmatch(
        r'module\.(\w+)\["(demo|api|worker|analytics)"\]\.(aws_\w+)\.(\w+)(?:\[(?:"[^"\\]+"|[0-9]+)\])?',
        address,
    )
    require(match is not None, "Unexpected application resource address")
    module, instance, kind, name = match.groups()
    require(
        (module == "service" and instance != "demo")
        or (module != "service" and instance == "demo"),
        "Unexpected module instance",
    )
    require(
        kind == resource["type"] and name in RESOURCES.get(module, {}).get(kind, set()),
        "Resource is outside the reviewed application allowlist",
    )
    change = resource["change"]
    values = change.get("before" if mode == "destroy" else "after") or {}
    if change["actions"] == ["no-op"]:
        return FIELDS
    if mode == "destroy":
        if kind == "aws_db_instance":
            require(
                values.get("skip_final_snapshot") is False
                and values.get("final_snapshot_identifier", "").startswith("canopyops-demo-"),
                "Database teardown requires an owned final snapshot",
            )
        require(
            module != "foundation", "Retained foundation resources are outside application teardown"
        )
        return FIELDS
    unknown = change.get("after_unknown") or {}

    def fixed(key, expected):
        require(not unknown.get(key) and values.get(key) == expected, "Unsafe or unknown " + key)

    if kind == "aws_iam_role":
        boundary = application.get("permissions_boundary_arn", "")
        require(
            re.fullmatch(r"arn:aws:iam::[0-9]{12}:policy/canopyops-demo-runtime-boundary", boundary)
            is not None,
            "Wrong runtime boundary",
        )
        fixed("permissions_boundary", boundary)
        # Unknown computed IAM JSON is rejected instead of silently trusted.
        require(not unknown.get("assume_role_policy"), "Role trust must be reviewable")
        trust = json.loads(values.get("assume_role_policy", "{}"))
        require(
            all(
                s.get("Effect") == "Allow"
                and s.get("Principal") == {"Service": "ecs-tasks.amazonaws.com"}
                and s.get("Action") == "sts:AssumeRole"
                and s.get("Condition", {}).get("StringEquals", {}).get("aws:SourceAccount")
                == boundary.split(":")[4]
                for s in trust.get("Statement", [])
            )
            and bool(trust.get("Statement")),
            "Unexpected role trust",
        )
    if kind == "aws_iam_role_policy" and not unknown.get("policy"):
        policy = json.loads(values.get("policy", "{}"))
        require(bool(policy.get("Statement")), "Empty runtime policy")
        for statement in policy["Statement"]:
            actions = statement.get("Action", [])
            actions = [actions] if isinstance(actions, str) else actions
            require(
                statement.get("Effect") == "Allow"
                and all(
                    not a.lower().startswith(("iam:", "sts:"))
                    and a not in {"*", "s3:DeleteObject", "secretsmanager:PutSecretValue"}
                    for a in actions
                ),
                "Runtime policy exceeds its role",
            )
            require(
                statement.get("Resource") != "*" or actions == ["ecr:GetAuthorizationToken"],
                "Unscoped runtime policy",
            )
    if kind == "aws_db_instance":
        fixed("publicly_accessible", False)
        fixed("storage_encrypted", True)
        fixed("multi_az", True)
        fixed("skip_final_snapshot", False)
        fixed("manage_master_user_password", True)
        fixed("deletion_protection", not application.get("teardown_prepared", False))
        require(
            values.get("final_snapshot_identifier", "").startswith("canopyops-demo-"),
            "Unexpected final snapshot namespace",
        )
        require(
            type(values.get("allocated_storage")) is int
            and 20 <= values["allocated_storage"] <= 100,
            "Database storage exceeds scope",
        )
    if kind == "aws_elasticache_replication_group":
        fixed("transit_encryption_enabled", True)
        fixed("at_rest_encryption_enabled", True)
        fixed("transit_encryption_mode", "required")
        require(values.get("num_cache_clusters") in [1, 2], "Unbounded cache node count")
    if kind == "aws_ecs_task_definition":
        component = "generator" if module == "foundation" else instance
        fixed("family", "canopyops-demo-" + component)
        fixed("network_mode", "awsvpc")
        fixed("skip_destroy", True)
        fixed("cpu", "256" if component == "generator" else "512")
        fixed("memory", "512" if component == "generator" else "1024")
        require(
            values.get("requires_compatibilities") == ["FARGATE"], "Unexpected task launch type"
        )
        account = application.get("permissions_boundary_arn", "").split(":")
        require(
            len(account) == 6 and re.fullmatch(r"[0-9]{12}", account[4]) is not None,
            "Missing task account",
        )
        owner = account[4]
        image_component = "worker" if component == "generator" else component
        image = application.get("image_uris", {}).get(image_component, "")
        require(
            re.fullmatch(
                owner
                + r"\.dkr\.ecr\.eu-west-2\.amazonaws\.com/canopyops-demo/"
                + image_component
                + r"@sha256:[a-f0-9]{64}",
                image,
            )
            is not None,
            "Unreviewed task image",
        )
        for field, suffix in [("execution_role_arn", "execution"), ("task_role_arn", "task")]:
            if not unknown.get(field):
                fixed(
                    field,
                    "arn:aws:iam::" + owner + ":role/canopyops-demo-" + component + "-" + suffix,
                )
        if not unknown.get("container_definitions"):
            containers = json.loads(values.get("container_definitions", "[]"))
            require(len(containers) == 1, "Unexpected sidecars")
            container = containers[0]
            require(
                container.get("image") == image and container.get("name") == component,
                "Container identity changed",
            )
            require(
                container.get("user") == "10001:10001"
                and container.get("readonlyRootFilesystem") is True
                and not container.get("privileged"),
                "Writable or privileged container",
            )
            require(
                container.get("linuxParameters", {}).get("capabilities", {}).get("drop") == ["ALL"],
                "Container capabilities changed",
            )
            require(
                not container.get("entryPoint") and not container.get("mountPoints"),
                "Unexpected entrypoint or mount",
            )
            expected_command = (
                ["python", "-m", "ingestion.producer", "--cycles", "1"]
                if component == "generator"
                else None
            )
            require(container.get("command") == expected_command, "Unexpected startup override")
            for setting in container.get("environment", []):
                require(
                    not re.search(
                        r"(?i)(password|token|credential|secret|api.?key)", setting.get("name", "")
                    ),
                    "Plaintext credential setting",
                )
            for setting in container.get("secrets", []):
                permitted = "(" + component + ("|redis" if component == "analytics" else "") + ")"
                require(
                    re.fullmatch(
                        "arn:aws:secretsmanager:eu-west-2:"
                        + owner
                        + ":secret:canopyops-demo/"
                        + permitted
                        + r"-[A-Za-z0-9]{6}(?::(?:username|password)::)?",
                        setting.get("valueFrom", ""),
                    )
                    is not None,
                    "Unexpected runtime secret reference",
                )
            logs = container.get("logConfiguration", {})
            require(
                logs.get("logDriver") == "awslogs"
                and logs.get("options", {}).get("awslogs-region") == "eu-west-2"
                and logs.get("options", {}).get("awslogs-group")
                == "/ecs/canopyops-demo-" + component,
                "Unexpected log delivery",
            )
    if kind == "aws_ecs_service":
        require(values.get("desired_count") in [0, 1], "Unreviewed task count")
        require(
            all(n.get("assign_public_ip") is False for n in values.get("network_configuration", []))
            and bool(values.get("network_configuration")),
            "Public or unknown ECS networking",
        )
        require(
            all(
                d.get("enable") and d.get("rollback")
                for d in values.get("deployment_circuit_breaker", [])
            )
            and bool(values.get("deployment_circuit_breaker")),
            "Missing deployment rollback",
        )
    if kind == "aws_vpc_endpoint":
        fixed("vpc_endpoint_type", "Gateway" if name == "s3" else "Interface")
        if name == "interface":
            fixed("private_dns_enabled", True)
    if kind in {"aws_vpc_security_group_ingress_rule", "aws_vpc_security_group_egress_rule"}:
        fixed("ip_protocol", "tcp")
        public = module == "security" and name == "alb_public"
        if public:
            require(
                values.get("from_port") in [80, 443]
                and values.get("from_port") == values.get("to_port")
                and values.get("cidr_ipv4") == "0.0.0.0/0"
                and not values.get("cidr_ipv6"),
                "Unexpected public ingress",
            )
        else:
            require(
                not values.get("cidr_ipv4")
                and not values.get("cidr_ipv6")
                and not unknown.get("cidr_ipv4")
                and not unknown.get("cidr_ipv6"),
                "Public service, database, cache or metrics flow",
            )
            require(
                values.get("from_port") in [443, 6379, 5432, 8000, 8081]
                and values.get("from_port") == values.get("to_port"),
                "Unexpected service port",
            )
    if kind == "aws_route53_record":
        fixed("zone_id", application.get("hosted_zone_id"))
    if kind == "aws_acm_certificate":
        fixed("domain_name", application.get("domain_name"))
        fixed("validation_method", "DNS")
        require(
            set(values.get("subject_alternative_names") or [])
            == {"analytics." + application.get("domain_name", "")},
            "Unexpected certificate SAN",
        )
    if kind == "aws_route53_record" and not unknown.get("name"):
        domain = application.get("domain_name", "")
        host = values.get("name", "").rstrip(".")
        require(
            bool(domain)
            and (
                host in {domain, "analytics." + domain}
                or host.startswith("_")
                and host.endswith("." + domain)
            ),
            "Unrelated DNS record",
        )
        require(values.get("type") in ["A", "CNAME"], "Unexpected DNS record type")
    if kind == "aws_s3_bucket_public_access_block":
        for key in [
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        ]:
            fixed(key, True)
    if kind == "aws_s3_bucket":
        fixed("force_destroy", False)
    if kind == "aws_kms_key":
        fixed("enable_key_rotation", True)
    if kind == "aws_cloudwatch_log_group":
        require(values.get("retention_in_days") in [1, 3, 5, 7, 14, 30], "Unbounded logs")
    if kind == "aws_secretsmanager_secret":
        require(values.get("recovery_window_in_days", 0) >= 7, "Secret recovery window removed")
    if kind == "aws_sqs_queue":
        fixed("sqs_managed_sse_enabled", True)
    return FIELDS

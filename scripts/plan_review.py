"""Allowlisted plan-review metadata; never render policies, environment or credentials."""

import re

SCALARS = {
    "aws_db_parameter_group": ["parameter"],
    "aws_db_instance": [
        "instance_class",
        "allocated_storage",
        "max_allocated_storage",
        "storage_type",
        "port",
        "backup_retention_period",
        "skip_final_snapshot",
        "final_snapshot_identifier",
        "delete_automated_backups",
        "manage_master_user_password",
        "iam_database_authentication_enabled",
        "auto_minor_version_upgrade",
        "allow_major_version_upgrade",
        "engine_lifecycle_support",
        "parameter_group_name",
        "db_subnet_group_name",
        "vpc_security_group_ids",
        "kms_key_id",
        "enabled_cloudwatch_logs_exports",
    ],
    "aws_vpc_endpoint": [
        "service_name",
        "vpc_id",
        "subnet_ids",
        "security_group_ids",
        "route_table_ids",
        "prefix_list_id",
    ],
    "aws_security_group": ["vpc_id"],
    "aws_vpc_security_group_ingress_rule": [
        "security_group_id",
        "referenced_security_group_id",
        "prefix_list_id",
    ],
    "aws_vpc_security_group_egress_rule": [
        "security_group_id",
        "referenced_security_group_id",
        "prefix_list_id",
    ],
    "aws_cloudwatch_log_group": ["retention_in_days", "kms_key_id"],
    "aws_kms_key": ["enable_key_rotation", "deletion_window_in_days"],
    "aws_s3_bucket": ["bucket", "force_destroy"],
    "aws_s3_bucket_public_access_block": [
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ],
    "aws_secretsmanager_secret": ["recovery_window_in_days", "kms_key_id"],
    "aws_ecs_task_definition": [
        "network_mode",
        "requires_compatibilities",
        "execution_role_arn",
        "task_role_arn",
        "skip_destroy",
    ],
    "aws_ecs_service": [
        "launch_type",
        "platform_version",
        "enable_execute_command",
        "deployment_minimum_healthy_percent",
        "deployment_maximum_percent",
        "wait_for_steady_state",
    ],
    "aws_lb": [
        "internal",
        "load_balancer_type",
        "ip_address_type",
        "subnets",
        "security_groups",
        "enable_deletion_protection",
        "drop_invalid_header_fields",
        "desync_mitigation_mode",
    ],
    "aws_lb_listener": ["port", "protocol", "ssl_policy", "certificate_arn"],
    "aws_lb_listener_rule": ["priority"],
    "aws_lb_target_group": ["port", "protocol", "target_type", "deregistration_delay"],
    "aws_elasticache_replication_group": [
        "node_type",
        "num_cache_clusters",
        "transit_encryption_enabled",
        "transit_encryption_mode",
        "at_rest_encryption_enabled",
        "user_group_ids",
        "automatic_failover_enabled",
        "multi_az_enabled",
    ],
    "aws_sqs_queue": [
        "sqs_managed_sse_enabled",
        "message_retention_seconds",
        "visibility_timeout_seconds",
        "receive_wait_time_seconds",
        "max_message_size",
    ],
}
BLOCKS = {
    "network_configuration": {"subnets": None, "security_groups": None, "assign_public_ip": None},
    "deployment_circuit_breaker": {"enable": None, "rollback": None},
    "runtime_platform": {"cpu_architecture": None, "operating_system_family": None},
    "health_check": {
        "path": None,
        "protocol": None,
        "port": None,
        "matcher": None,
        "interval": None,
        "timeout": None,
        "healthy_threshold": None,
        "unhealthy_threshold": None,
    },
    "access_logs": {"bucket": None, "prefix": None, "enabled": None},
    "default_action": {
        "type": None,
        "redirect": {"port": None, "protocol": None, "status_code": None},
        "fixed_response": {"status_code": None},
    },
    "action": {
        "type": None,
        "target_group_arn": None,
        "redirect": {"port": None, "protocol": None, "status_code": None},
        "fixed_response": {"status_code": None},
    },
    "versioning_configuration": {"status": None},
    "rule": {
        "id": None,
        "status": None,
        "bucket_key_enabled": None,
        "apply_server_side_encryption_by_default": {
            "sse_algorithm": None,
            "kms_master_key_id": None,
        },
        "expiration": {"days": None},
        "noncurrent_version_expiration": {"noncurrent_days": None},
    },
}
BLOCK_KINDS = {
    "aws_ecs_service": ["network_configuration", "deployment_circuit_breaker"],
    "aws_ecs_task_definition": ["runtime_platform"],
    "aws_lb": ["access_logs"],
    "aws_lb_listener": ["default_action"],
    "aws_lb_listener_rule": ["action"],
    "aws_lb_target_group": ["health_check"],
    "aws_s3_bucket_versioning": ["versioning_configuration"],
    "aws_s3_bucket_server_side_encryption_configuration": ["rule"],
    "aws_s3_bucket_lifecycle_configuration": ["rule"],
}
DOCUMENTS = {
    "aws_iam_role": ["assume_role_policy"],
    "aws_iam_role_policy": ["policy"],
    "aws_kms_key": ["policy"],
    "aws_s3_bucket_policy": ["policy"],
    "aws_sqs_queue_policy": ["policy"],
    "aws_sqs_queue": ["redrive_policy"],
    "aws_sqs_queue_redrive_allow_policy": ["redrive_allow_policy"],
    "aws_vpc_endpoint": ["policy"],
    "aws_ecs_task_definition": ["container_definitions"],
}


def fields(kind, base):
    return list(dict.fromkeys([*base, *SCALARS.get(kind, []), *BLOCK_KINDS.get(kind, [])]))


def scalar(value):
    return value is None or type(value) in {str, bool, int, float}


def project(value, shape):
    if shape is None:
        if scalar(value):
            return value
        if isinstance(value, list) and all(scalar(v) for v in value):
            return value
        return "(unsupported value omitted)"
    if isinstance(value, list):
        return [project(item, shape) for item in value]
    if not isinstance(value, dict):
        return "(unsupported block omitted)"
    return {key: project(value[key], child) for key, child in shape.items() if key in value}


def select(values, kind, names):
    if kind == "aws_db_parameter_group":
        parameters = values.get("parameter", [])
        force_ssl = (
            [
                p.get("value")
                for p in parameters
                if isinstance(p, dict) and p.get("name") == "rds.force_ssl"
            ]
            if isinstance(parameters, list)
            else []
        )
        result = {
            key: project(values[key], None) for key in names if key in values and key != "parameter"
        }
        result["rds_force_ssl"] = force_ssl == ["1"] if force_ssl else None
        return result
    return {
        key: project(values[key], BLOCKS[key] if key in BLOCK_KINDS.get(kind, []) else None)
        for key in names
        if key in values
    }


def references(plan, address, names):
    """Render expression references only, never constant values from configuration."""
    address = re.sub(r"\[[^]]*\]", "", address)
    parts = address.split(".")
    module = plan.get("configuration", {}).get("root_module", {})
    while len(parts) > 2 and parts[0] == "module":
        module = module.get("module_calls", {}).get(parts[1], {}).get("module", {})
        parts = parts[2:]
    target = ".".join(parts)
    resource = next(
        (
            r
            for r in module.get("resources", [])
            if re.sub(r"\[[^]]*\]", "", r.get("address", "")) in {target, address}
        ),
        {},
    )

    def collect(expression):
        found = set()
        if isinstance(expression, dict):
            for ref in expression.get("references", []):
                if (
                    isinstance(ref, str)
                    and len(ref) <= 250
                    and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.\[\]"-]*', ref)
                ):
                    found.add(ref)
            for key, value in expression.items():
                if key != "constant_value":
                    found.update(collect(value))
        elif isinstance(expression, list):
            for value in expression:
                found.update(collect(value))
        return found

    return {
        name: refs
        for name in names
        if (refs := sorted(collect(resource.get("expressions", {}).get(name, {}))))
    }

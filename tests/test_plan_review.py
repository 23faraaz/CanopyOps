"""Sanitized review tests: fictional plan dictionaries; no state, files or AWS."""

from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_review
import terraform_automation as automation
from test_terraform_automation import plan_value, deployment


def test_allowlist_exposes_sizing_and_security_but_never_credentials():
    values = {
        "instance_class": "db.t4g.micro",
        "allocated_storage": 20,
        "backup_retention_period": 7,
        "vpc_security_group_ids": ["sg-fixture"],
        "password": "DO-NOT-PRINT",
        "master_user_secret": [{"secret_string": "DO-NOT-PRINT"}],
    }
    result = plan_review.select(
        values, "aws_db_instance", plan_review.fields("aws_db_instance", [])
    )
    assert result == {
        k: v for k, v in values.items() if k not in {"password", "master_user_secret"}
    }
    assert "DO-NOT-PRINT" not in json.dumps(result)


def test_nested_network_values_are_allowlisted_and_sensitivity_is_preserved():
    values = {
        "network_configuration": [
            {
                "subnets": ["subnet-fixture"],
                "security_groups": ["sg-fixture"],
                "assign_public_ip": False,
                "secret": "DO-NOT-PRINT",
            }
        ]
    }
    result = plan_review.select(
        values, "aws_ecs_service", plan_review.fields("aws_ecs_service", [])
    )
    assert result["network_configuration"][0]["assign_public_ip"] is False
    assert "secret" not in json.dumps(result)
    masked = automation.redacted(result, {"network_configuration": [{"subnets": True}]})
    assert masked["network_configuration"][0]["subnets"] == "(sensitive)"


def test_listener_projection_preserves_tls_redirect_and_blocks_without_response_body():
    value = {
        "protocol": "HTTPS",
        "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
        "default_action": [
            {
                "type": "fixed-response",
                "fixed_response": [{"status_code": "403", "message_body": "DO-NOT-PRINT"}],
            }
        ],
    }
    result = plan_review.select(value, "aws_lb_listener", plan_review.fields("aws_lb_listener", []))
    assert result["ssl_policy"] == value["ssl_policy"]
    assert result["default_action"][0]["fixed_response"] == [{"status_code": "403"}]
    assert "DO-NOT-PRINT" not in json.dumps(result)


def test_database_tls_summary_does_not_render_other_parameter_values():
    value = {
        "parameter": [
            {"name": "rds.force_ssl", "value": "1"},
            {"name": "other", "value": "DO-NOT-PRINT"},
        ]
    }
    result = plan_review.select(value, "aws_db_parameter_group", ["parameter"])
    assert result == {"rds_force_ssl": True}
    value["parameter"][0]["value"] = "0"
    assert (
        plan_review.select(value, "aws_db_parameter_group", ["parameter"])["rds_force_ssl"] is False
    )


def test_only_configured_security_document_types_are_flagged():
    assert "aws_s3_bucket" not in plan_review.DOCUMENTS
    assert "aws_secretsmanager_secret" not in plan_review.DOCUMENTS
    assert plan_review.DOCUMENTS["aws_kms_key"] == ["policy"]
    assert plan_review.DOCUMENTS["aws_ecs_task_definition"] == ["container_definitions"]


def test_computed_fields_retain_only_source_references_not_constant_values():
    plan = {
        "configuration": {
            "root_module": {
                "module_calls": {
                    "service": {
                        "module": {
                            "resources": [
                                {
                                    "address": "aws_ecs_task_definition.this",
                                    "expressions": {
                                        "container_definitions": {
                                            "references": [
                                                "var.image_uri",
                                                "aws_cloudwatch_log_group.this.name",
                                            ],
                                            "constant_value": "DO-NOT-PRINT",
                                        },
                                        "password": {"constant_value": "DO-NOT-PRINT"},
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    result = plan_review.references(
        plan, 'module.service["api"].aws_ecs_task_definition.this', ["container_definitions"]
    )
    assert result == {
        "container_definitions": ["aws_cloudwatch_log_group.this.name", "var.image_uri"]
    }
    assert "DO-NOT-PRINT" not in json.dumps(result)


def test_review_renders_rule_group_references_with_existing_sensitive_masks():
    plan = plan_value()
    plan["variables"]["application"] = {
        "value": {
            "permissions_boundary_arn": "arn:aws:iam::123456789012:policy/canopyops-demo-runtime-boundary"
        }
    }
    plan["resource_changes"] = [
        {
            "address": 'module.security["demo"].aws_vpc_security_group_ingress_rule.connection["worker_to_postgres"]',
            "type": "aws_vpc_security_group_ingress_rule",
            "change": {
                "actions": ["create"],
                "before": None,
                "after": {
                    "ip_protocol": "tcp",
                    "from_port": 5432,
                    "to_port": 5432,
                    "security_group_id": "sg-target",
                    "referenced_security_group_id": "sg-worker",
                    "cidr_ipv4": None,
                    "cidr_ipv6": None,
                },
                "after_unknown": {"security_group_id": True},
                "after_sensitive": {"referenced_security_group_id": True},
            },
        }
    ]
    result = automation.review_plan(plan, "apply")[0]
    assert "security_group_id" in result["computed_after"]
    assert result["after"]["referenced_security_group_id"] == "(sensitive)"
    assert "sg-worker" not in json.dumps(result)


def test_unknown_policy_is_marked_and_raw_content_is_never_rendered():
    plan = plan_value()
    plan["variables"]["application"] = {"value": deployment()}
    plan["resource_changes"] = [
        {
            "address": 'module.connectivity["demo"].aws_vpc_endpoint.interface["ecr.api"]',
            "type": "aws_vpc_endpoint",
            "change": {
                "actions": ["create"],
                "before": None,
                "after": {
                    "vpc_endpoint_type": "Interface",
                    "private_dns_enabled": True,
                    "subnet_ids": [None, None],
                    "policy": "DO-NOT-PRINT",
                },
                "after_unknown": {"policy": True, "subnet_ids": [True, True]},
            },
        }
    ]
    result = automation.review_plan(plan, "apply")[0]
    assert result["computed_security_documents"] == ["policy"]
    assert "subnet_ids" in result["computed_after"]
    assert "DO-NOT-PRINT" not in json.dumps(result)

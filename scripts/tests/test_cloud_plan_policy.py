"""Application plan-policy tests use in-memory fictional dictionaries only."""

import copy
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/cloud_plan_policy.py"
spec = importlib.util.spec_from_file_location("cloud_plan_guard", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
APPLICATION = {
    "permissions_boundary_arn": "arn:aws:iam::123456789012:policy/canopyops-demo-runtime-boundary",
    "hosted_zone_id": "ZTEST123",
}


def resource(kind, module, name, values, actions=None):
    return {
        "address": f'module.{module}["demo"].{kind}.{name}',
        "type": kind,
        "change": {
            "actions": actions or ["create"],
            "after": values,
            "before": values,
            "after_unknown": {},
        },
    }


class PolicyTests(unittest.TestCase):
    def database(self):
        return resource(
            "aws_db_instance",
            "database",
            "this",
            {
                "publicly_accessible": False,
                "storage_encrypted": True,
                "multi_az": True,
                "skip_final_snapshot": False,
                "manage_master_user_password": True,
                "allocated_storage": 20,
                "deletion_protection": True,
                "final_snapshot_identifier": "canopyops-demo-final-test",
            },
        )

    def test_private_encrypted_multi_az_database_is_accepted(self):
        guard.check(self.database(), APPLICATION, "apply")

    def test_public_or_unencrypted_database_is_rejected(self):
        for field, value in [
            ("publicly_accessible", True),
            ("storage_encrypted", False),
            ("multi_az", False),
            ("skip_final_snapshot", True),
            ("allocated_storage", 101),
        ]:
            entry = self.database()
            entry["change"]["after"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                guard.check(entry, APPLICATION, "apply")

    def test_unknown_security_flags_are_rejected(self):
        entry = self.database()
        entry["change"]["after_unknown"]["publicly_accessible"] = True
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_secret_versions_are_outside_scope(self):
        entry = resource("aws_secretsmanager_secret_version", "foundation", "runtime", {})
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_missing_application_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            guard.check(self.database(), None, "apply")

    def test_unapproved_resource_name_is_rejected(self):
        entry = self.database()
        entry["address"] = entry["address"].replace(".this", ".other")
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_outbox_deletion_is_rejected(self):
        for kind in ["aws_s3_bucket", "aws_kms_key", "aws_s3_bucket_policy"]:
            entry = resource(kind, "foundation", "outbox", {}, ["delete"])
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                guard.check(entry, APPLICATION, "destroy")

    def test_private_service_flows_only(self):
        entry = resource(
            "aws_vpc_security_group_ingress_rule",
            "security",
            "connection",
            {
                "ip_protocol": "tcp",
                "from_port": 5432,
                "to_port": 5432,
                "cidr_ipv4": None,
                "cidr_ipv6": None,
            },
        )
        guard.check(entry, APPLICATION, "apply")
        entry["change"]["after"]["cidr_ipv4"] = "0.0.0.0/0"
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_public_metrics_are_rejected(self):
        entry = resource(
            "aws_vpc_security_group_ingress_rule",
            "security",
            "alb_public",
            {"ip_protocol": "tcp", "from_port": 9100, "to_port": 9100, "cidr_ipv4": "0.0.0.0/0"},
        )
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_private_dns_cannot_be_disabled(self):
        entry = resource(
            "aws_vpc_endpoint",
            "connectivity",
            "interface",
            {"vpc_endpoint_type": "Interface", "private_dns_enabled": False},
        )
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_wrong_zone_is_rejected(self):
        entry = resource("aws_route53_record", "ingress", "application", {"zone_id": "ZOTHER"})
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_privileged_runtime_policy_is_rejected(self):
        import json

        entry = resource(
            "aws_iam_role_policy",
            "foundation",
            "execution",
            {
                "policy": json.dumps(
                    {
                        "Statement": [
                            {"Effect": "Allow", "Action": ["iam:CreateUser"], "Resource": "*"}
                        ]
                    }
                )
            },
        )
        with self.assertRaises(ValueError):
            guard.check(entry, APPLICATION, "apply")

    def test_role_requires_exact_boundary_and_ecs_trust(self):
        import json

        values = {
            "permissions_boundary": APPLICATION["permissions_boundary_arn"],
            "assume_role_policy": json.dumps(
                {
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "sts:AssumeRole",
                            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                            "Condition": {"StringEquals": {"aws:SourceAccount": "123456789012"}},
                        }
                    ]
                }
            ),
        }
        entry = resource("aws_iam_role", "foundation", "task", values)
        guard.check(entry, APPLICATION, "apply")
        changed = copy.deepcopy(entry)
        changed["change"]["after"]["permissions_boundary"] = (
            "arn:aws:iam::123456789012:policy/AdministratorAccess"
        )
        with self.assertRaises(ValueError):
            guard.check(changed, APPLICATION, "apply")


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys

        sys.path.insert(0, str(SOURCE.parent))
        import terraform_automation

        cls.controller = terraform_automation

    def task(self):
        import json

        image = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/canopyops-demo/api@sha256:" + "a" * 64
        application = {**APPLICATION, "image_uris": {"api": image}}
        container = {
            "name": "api",
            "image": image,
            "user": "10001:10001",
            "readonlyRootFilesystem": True,
            "linuxParameters": {"capabilities": {"drop": ["ALL"]}},
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-region": "eu-west-2",
                    "awslogs-group": "/ecs/canopyops-demo-api",
                },
            },
        }
        values = {
            "family": "canopyops-demo-api",
            "network_mode": "awsvpc",
            "skip_destroy": True,
            "cpu": "512",
            "memory": "1024",
            "requires_compatibilities": ["FARGATE"],
            "execution_role_arn": "arn:aws:iam::123456789012:role/canopyops-demo-api-execution",
            "task_role_arn": "arn:aws:iam::123456789012:role/canopyops-demo-api-task",
            "container_definitions": json.dumps([container]),
        }
        entry = resource("aws_ecs_task_definition", "service", "this", values)
        entry["address"] = entry["address"].replace('["demo"]', '["api"]')
        return entry, application

    def plan(self, entry, application):
        return {
            "complete": True,
            "variables": {
                "project_name": {"value": "canopyops"},
                "environment": {"value": "demo"},
                "aws_region": {"value": "eu-west-2"},
                "application": {"value": application},
            },
            "resource_changes": [entry],
        }

    def test_safe_immutable_task_replacement(self):
        entry, application = self.task()
        entry["change"]["actions"] = ["create", "delete"]
        rows = self.controller.review_plan(self.plan(entry, application), "apply")
        self.assertEqual(rows[0]["actions"], ["create", "delete"])
        self.assertNotIn("container_definitions", rows[0]["after"])

    def generator_cleanup_plan(self):
        import json

        entry, application = self.task()
        image = application["image_uris"].pop("api").replace("/api@", "/worker@")
        application["image_uris"]["worker"] = image
        entry["address"] = 'module.foundation["demo"].aws_ecs_task_definition.generator[0]'
        values = entry["change"]["after"]
        values.update(family="canopyops-demo-generator", cpu="256", memory="512")
        for key in ["execution_role_arn", "task_role_arn"]:
            values[key] = values[key].replace("-api-", "-generator-")
        container = json.loads(values["container_definitions"])[0]
        container.update(
            name="generator",
            image=image,
            command=["python", "-m", "ingestion.producer", "--cycles", "1"],
        )
        container["logConfiguration"]["options"]["awslogs-group"] = "/ecs/canopyops-demo-generator"
        values["container_definitions"] = json.dumps([container])
        old = copy.deepcopy(entry)
        old["deposed"] = "deadbeef"
        old["change"].update(actions=["delete"], after=None)
        old["change"]["before"]["arn"] = (
            "arn:aws:ecs:eu-west-2:123456789012:task-definition/canopyops-demo-generator:1"
        )
        entry["change"].update(
            actions=["create", "delete"], replace_paths=[["container_definitions"]]
        )
        plan = self.plan(entry, application)
        plan["resource_changes"].insert(0, old)
        return plan

    def test_deposed_retained_generator_cleanup_is_reviewable(self):
        rows = self.controller.review_plan(self.generator_cleanup_plan(), "apply")
        self.assertTrue(rows[0]["retained_deposed_revision"])
        self.assertEqual(rows[0]["actions"], ["delete"])
        self.assertEqual(rows[1]["replacement_fields"], ["container_definitions"])
        self.assertNotIn("container_definitions", rows[0]["before"])

    def test_generator_cleanup_rejects_missing_evidence(self):
        for mutation in [
            "not_deposed",
            "invalid_marker",
            "deregister",
            "wrong_account",
            "no_current",
            "wrong_family",
            "unsafe_current",
        ]:
            plan = self.generator_cleanup_plan()
            old, current = plan["resource_changes"]
            if mutation == "not_deposed":
                old.pop("deposed")
            elif mutation == "invalid_marker":
                old["deposed"] = "unsafe-value"
            elif mutation == "deregister":
                old["change"]["before"]["skip_destroy"] = False
            elif mutation == "wrong_account":
                old["change"]["before"]["arn"] = old["change"]["before"]["arn"].replace(
                    "123456789012", "999999999999"
                )
            elif mutation == "no_current":
                plan["resource_changes"].pop()
            elif mutation == "wrong_family":
                old["change"]["before"]["family"] = "unrelated-project"
            else:
                current["change"]["after"]["skip_destroy"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.controller.review_plan(plan, "apply")

    def test_destroy_first_replacement_is_rejected(self):
        entry, application = self.task()
        entry["change"]["actions"] = ["delete", "create"]
        with self.assertRaises(ValueError):
            self.controller.review_plan(self.plan(entry, application), "apply")

    def test_privileged_container_is_rejected(self):
        import json

        for change in [{"user": "0"}, {"privileged": True}, {"readonlyRootFilesystem": False}]:
            entry, application = self.task()
            containers = json.loads(entry["change"]["after"]["container_definitions"])
            containers[0].update(change)
            entry["change"]["after"]["container_definitions"] = json.dumps(containers)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controller.review_plan(self.plan(entry, application), "apply")

    def test_computed_container_is_flagged_for_source_review(self):
        entry, application = self.task()
        entry["change"]["after"]["container_definitions"] = None
        entry["change"]["after_unknown"]["container_definitions"] = True
        rows = self.controller.review_plan(self.plan(entry, application), "apply")
        self.assertEqual(rows[0]["computed_security_documents"], ["container_definitions"])

    def test_bootstrap_and_registry_remain_forbidden(self):
        entry, application = self.task()
        for address in [
            "module.application_boundary[0].aws_iam_policy.runtime_boundary",
            "aws_s3_bucket.terraform_state",
            'module.registry.aws_ecr_repository.this["api"]',
        ]:
            changed = copy.deepcopy(entry)
            changed["address"] = address
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.controller.review_plan(self.plan(changed, application), "apply")

    def test_network_plan_keeps_existing_review(self):
        entry = resource("aws_vpc", "network", "this", {"cidr_block": "10.42.0.0/16"})
        entry["address"] = "module.network.aws_vpc.this"
        rows = self.controller.review_plan(self.plan(entry, None), "apply")
        self.assertEqual(rows[0]["after"]["cidr_block"], "10.42.0.0/16")


class TargetedDestroyTests(unittest.TestCase):
    def controller(self):
        import sys

        sys.path.insert(0, str(SOURCE.parent))
        import terraform_automation

        return terraform_automation

    def test_partial_scope_requires_explicit_destroy_mode(self):
        plan = {
            "complete": False,
            "variables": {
                name: {"value": value}
                for name, value in {
                    "project_name": "canopyops",
                    "environment": "demo",
                    "aws_region": "eu-west-2",
                }.items()
            },
            "resource_changes": [],
        }
        with self.assertRaises(ValueError):
            self.controller().review_plan(plan, "apply", targeted_destroy=True)
        with self.assertRaises(ValueError):
            self.controller().review_plan(plan, "destroy")
        self.assertEqual(self.controller().review_plan(plan, "destroy", targeted_destroy=True), [])

    def test_deferred_changes_are_rejected_even_for_teardown(self):
        plan = {"complete": False, "deferred_changes": [{"reason": "unknown"}]}
        with self.assertRaises(ValueError):
            self.controller().review_plan(plan, "destroy", targeted_destroy=True)


if __name__ == "__main__":
    unittest.main()

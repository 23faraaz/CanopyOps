"""AWS-credential-free Terraform checks in disposable source-only Docker workspaces.

Requires WSL/Linux, Docker and Git. Never initializes the working checkout's backend.
Only tracked Terraform sources, examples and locks are copied; no local input or state.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = "hashicorp/terraform:1.17.2@sha256:6e9c1e647c31a960dc9b59e13272f9111d90010c10b717f77ffb324767660475"
TFLINT = "ghcr.io/terraform-linters/tflint:v0.66.0@sha256:65b9944a9274c4989492c1d79998c2801664bf8a55fddb4bd3a9e6d73a006ddc"
TRIVY = (
    "aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
)
ROOTS = ("infra", "infra/bootstrap", "infra/registry")


def allowed_source(name):
    path = Path(name)
    return (
        path.parts[0] == "infra"
        and not any(part in {".terraform", ".secrets", ".aws", "backups"} for part in path.parts)
        and (
            path.suffix == ".tf"
            or path.name.endswith(".tftest.hcl")
            or path.name in {".terraform.lock.hcl", ".tflint.hcl", "terraform.tfvars.example"}
        )
    )


def findings(report):
    return [
        {
            "id": finding["ID"],
            "severity": finding["Severity"],
            "file": result["Target"],
            "resource": finding.get("CauseMetadata", {}).get("Resource"),
            "message": finding["Message"],
        }
        for result in report.get("Results", [])
        for finding in result.get("Misconfigurations", [])
    ]


class Checks:
    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.source = directory / "source"
        self.source.mkdir()
        for name in ["home", "docker", "plugins", "data", "cache"]:
            (directory / name).mkdir()
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory / "home")}
        self.docker = [
            "docker",
            "--config",
            str(directory / "docker"),
            "--host",
            "unix:///var/run/docker.sock",
        ]
        self.evidence = ROOT / "backups" / ("terraform-checks-" + uuid.uuid4().hex)
        self.evidence.mkdir(parents=True)
        self.results = {
            "root": root,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "steps": [],
        }
        names = self.command(["git", "ls-files", "infra"]).stdout.splitlines()
        for name in names:
            if not allowed_source(name):
                continue
            if root == "infra" and any(name.startswith(child + "/") for child in ROOTS[1:]):
                continue
            if root == "infra/bootstrap" and not name.startswith("infra/bootstrap/"):
                continue
            if root == "infra/registry" and not name.startswith(
                ("infra/registry/", "infra/modules/ecr/")
            ):
                continue
            source = ROOT / name
            if source.is_symlink():
                raise ValueError("Refusing a source symlink: " + name)
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        config = ROOT / "infra/.tflint.hcl"
        if config.is_symlink():
            raise ValueError("Refusing a lint-config symlink")
        shutil.copyfile(config, directory / "tflint.hcl")

    def command(self, args, expected=0, extra_env=None):
        result = subprocess.run(
            args,
            cwd=ROOT,
            env={**self.env, **(extra_env or {})},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
        for value in (extra_env or {}).values():
            if value:
                result.stdout = result.stdout.replace(value, "[REDACTED]")
        if expected is not None and result.returncode != expected:
            print(result.stdout, flush=True)
            raise RuntimeError("Check command failed: " + " ".join(map(str, args[:3])))
        return result

    def run(self, label, image, args, network="none", expected=0, workdir=None, github_token=None):
        print("CHECK: " + self.root + " / " + label, flush=True)
        command = [
            *self.docker,
            "run",
            "--rm",
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=bind,source={self.directory},target=/work",
            "--workdir",
            workdir or "/work/source/" + self.root,
        ]
        for key, value in {
            "HOME": "/work/home",
            "TF_IN_AUTOMATION": "true",
            "TF_INPUT": "false",
            "CHECKPOINT_DISABLE": "1",
            "TF_DATA_DIR": "/work/data",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        }.items():
            command += ["--env", key + "=" + value]
        extra_env = {"GITHUB_TOKEN": github_token} if github_token else {}
        if extra_env:
            # Forward by name: the token never appears in Docker command arguments.
            command += ["--env", "GITHUB_TOKEN"]
        result = self.command([*command, image, *args], expected=None, extra_env=extra_env)
        (self.evidence / (label + ".log")).write_text(result.stdout)
        self.results["steps"].append({"name": label, "exit_code": result.returncode})
        if expected is not None and result.returncode != expected:
            print(result.stdout, flush=True)
            raise RuntimeError("Failed: " + label)
        return result

    def format(self):
        self.run("format", TERRAFORM, ["fmt", "-check", "-recursive", "."])

    def validate(self):
        self.run(
            "init",
            TERRAFORM,
            ["init", "-backend=false", "-input=false", "-lockfile=readonly", "-no-color"],
            network="bridge",
        )
        self.run("validate", TERRAFORM, ["validate", "-no-color"])
        if self.root == "infra/bootstrap":
            self.run("mock-policy-tests", TERRAFORM, ["test", "-no-color"])
        elif self.root == "infra/registry":
            self.run("mock-registry-tests", TERRAFORM, ["test", "-no-color"])

    def lint(self, github_token=None):
        self.run(
            "lint-init",
            TFLINT,
            ["--init", "--config=/work/tflint.hcl"],
            network="bridge",
            github_token=github_token,
        )
        directories = [self.root]
        if self.root == "infra":
            directories += [
                str(path.parent.relative_to(self.source))
                for path in (self.source / "infra/modules").rglob("*.tf")
                if path.read_text().strip()
            ]
        directories = sorted(set(directories))
        for index, directory in enumerate(directories):
            self.run(
                "lint-" + str(index),
                TFLINT,
                ["--config=/work/tflint.hcl", "--format=compact"],
                workdir="/work/source/" + directory,
            )

    def security(self):
        args = [
            "config",
            "--skip-check-update",
            "--misconfig-scanners",
            "terraform",
            "--format",
            "json",
            "--exit-code",
            "1",
            "--severity",
            "HIGH,CRITICAL",
            ".",
        ]
        result = self.run("security-gate", TRIVY, args, expected=None)
        # JSON written separately avoids relying on mixed scanner logs for evidence.
        json_start = result.stdout.find("{")
        report = json.loads(result.stdout[json_start:])
        (self.evidence / "security.json").write_text(json.dumps(report, indent=2) + "\n")
        self.results["blocking_findings"] = findings(report)
        if result.returncode != 0 or self.results["blocking_findings"] or not report.get("Results"):
            raise RuntimeError("HIGH/CRITICAL security gate failed; see retained report")
        advisory = self.run(
            "security-advisory", TRIVY, [*args[:-2], "LOW,MEDIUM", "."], expected=None
        )
        self.results["advisory_findings"] = findings(
            json.loads(advisory.stdout[advisory.stdout.find("{") :])
        )
        if advisory.returncode not in {0, 1}:
            raise RuntimeError("Advisory scanner failed")
        if self.root == "infra/bootstrap":
            self.waiver_controls(args)

    def waiver_controls(self, args):
        """Prove the native exception expires and cannot exempt another resource."""
        source = self.source / self.root / "main.tf"
        original = source.read_text()
        marker = re.search(
            r"^#trivy:ignore:AVD-AWS-0132:exp:\d{4}-\d{2}-\d{2}$", original, re.MULTILINE
        )
        if not marker:
            return
        canary = """
resource "aws_s3_bucket" "canary" {
  bucket = "canopyops-static-canary-never-created"
}
resource "aws_s3_bucket_server_side_encryption_configuration" "canary" {
  bucket = aws_s3_bucket.canary.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
"""
        controls = {
            "removed": original.replace(marker.group(), ""),
            "expired": original.replace(
                marker.group(), "#trivy:ignore:AVD-AWS-0132:exp:2000-01-01"
            ),
            "other-resource": original + canary,
        }
        self.results["waiver_controls"] = []
        try:
            for name, content in controls.items():
                source.write_text(content)
                result = self.run("waiver-" + name, TRIVY, args, expected=None)
                detected = findings(json.loads(result.stdout[result.stdout.find("{") :]))
                expected_resource = "aws_s3_bucket_server_side_encryption_configuration." + (
                    "canary" if name == "other-resource" else "terraform_state"
                )
                passed = result.returncode == 1 and any(
                    f["id"] == "AWS-0132" and f["resource"] == expected_resource for f in detected
                )
                self.results["waiver_controls"].append(
                    {"name": name, "passed": passed, "exit_code": result.returncode}
                )
                if not passed:
                    raise RuntimeError("Security exception control failed: " + name)
        finally:
            source.write_text(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", choices=ROOTS, default="infra")
    parser.add_argument("--only", choices=["format", "validate", "lint", "security"])
    parser.add_argument(
        "--github-token-from-env",
        action="store_true",
        help="Use the CI job token only for TFLint plugin download; requires --only lint",
    )
    args = parser.parse_args()
    if args.github_token_from_env and args.only != "lint":
        parser.error("--github-token-from-env requires --only lint")
    github_token = None
    if args.github_token_from_env:
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            parser.error("The explicitly requested CI job token is missing")
    with tempfile.TemporaryDirectory(prefix="canopy-terraform-", dir="/tmp") as temporary:
        check = Checks(args.root, Path(temporary))
        try:
            for name in ["format", "validate", "lint", "security"]:
                if args.only is None or args.only == name:
                    if name == "lint":
                        check.lint(github_token=github_token)
                    else:
                        getattr(check, name)()
            check.results["status"] = "passed"
        except Exception:
            check.results["status"] = "failed"
            raise
        finally:
            (check.evidence / "summary.json").write_text(json.dumps(check.results, indent=2) + "\n")
            print(json.dumps(check.results, indent=2), flush=True)
            print("Evidence: " + str(check.evidence), flush=True)


if __name__ == "__main__":
    main()

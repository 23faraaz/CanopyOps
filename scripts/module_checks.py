"""Check the five application modules using source-only, credential-free workspaces.

Never copies or opens tfvars, tfvars.example, environment, state or credential files.
Mock plans use inline fictional test inputs. Requires Linux/WSL and Docker.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from terraform_checks import TERRAFORM, TFLINT, TRIVY

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("queue", "database", "cache", "ecs-service", "ingress")


def sources(module, destination):
    source = ROOT / "infra/modules" / module
    destination.mkdir()
    paths = list(source.glob("*.tf")) + list((source / "tests").glob("*.tftest.hcl"))
    for path in paths:
        if path.is_symlink():
            raise ValueError("Refusing a module source symlink")
        target = destination / path.relative_to(source)
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
    # Public provider lock only. Never copy the root directory or input examples.
    shutil.copyfile(ROOT / "infra/.terraform.lock.hcl", destination / ".terraform.lock.hcl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=MODULES)
    parser.add_argument("--only", choices=["format", "validate", "lint", "security"])
    parser.add_argument(
        "--ci-plugin-token",
        action="store_true",
        help="Forward CI GITHUB_TOKEN only to TFLint plugin installation",
    )
    args = parser.parse_args()
    os.umask(0o077)
    evidence = ROOT / "backups" / ("module-checks-" + uuid.uuid4().hex)
    evidence.mkdir(parents=True)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": [], "status": "failed"}
    try:
        with tempfile.TemporaryDirectory(prefix="canopy-module-checks-") as temp:
            directory = Path(temp)
            for folder in ["home", "docker", "providers", "lint-plugins", "scanner-cache"]:
                (directory / folder).mkdir()
            environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory / "home")}
            docker = [
                "docker",
                "--config",
                str(directory / "docker"),
                "--host",
                "unix:///var/run/docker.sock",
            ]
            config = directory / "tflint.hcl"
            shutil.copyfile(ROOT / "infra/.tflint.hcl", config)
            # Isolate TFLint and Terraform caches while retaining the pinned ruleset.
            config.write_text(config.read_text().replace("/work/plugins", "/work/lint-plugins"))

            def run(module, step, image, arguments, network="none", plugin_token=False, expected=0):
                command = [
                    *docker,
                    "run",
                    "--rm",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--network",
                    network,
                    "--mount",
                    f"type=bind,source={directory},target=/work",
                    "--workdir",
                    f"/work/{module}",
                ]
                fixed = {
                    "HOME": "/work/home",
                    "CHECKPOINT_DISABLE": "1",
                    "TF_INPUT": "false",
                    "TF_IN_AUTOMATION": "true",
                    "TF_PLUGIN_CACHE_DIR": "/work/providers",
                    "AWS_CONFIG_FILE": "/dev/null",
                    "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "AWS_REGION": "eu-west-2",
                }
                for key, value in fixed.items():
                    command += ["--env", key + "=" + value]
                child_environment = environment
                if plugin_token and args.ci_plugin_token:
                    child_environment = {**environment, "GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]}
                    command += ["--env", "GITHUB_TOKEN"]
                print(f"CHECK: {module} / {step}", flush=True)
                completed = subprocess.run(
                    [*command, image, *arguments],
                    env=child_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                output = completed.stdout + completed.stderr
                (evidence / f"{module}-{step}.log").write_text(output)
                result["steps"].append(
                    {"module": module, "name": step, "exit_code": completed.returncode}
                )
                if completed.returncode != expected:
                    print(output[-14000:], flush=True)
                    raise RuntimeError(f"Module check failed: {module}/{step}")
                if step == "mock-contracts":
                    print(completed.stdout, flush=True)
                return completed.stdout

            modules = [args.module] if args.module else MODULES
            for module in modules:
                sources(module, directory / module)
                if args.only in [None, "format"]:
                    # This workspace contains exclusively selected .tf/.tftest.hcl files and the public lock.
                    run(module, "format", TERRAFORM, ["fmt", "-check", "-recursive", "-no-color"])
                if args.only in [None, "validate"]:
                    run(
                        module,
                        "init",
                        TERRAFORM,
                        [
                            "init",
                            "-backend=false",
                            "-input=false",
                            "-lockfile=readonly",
                            "-no-color",
                        ],
                        network="bridge",
                    )
                    run(module, "validate", TERRAFORM, ["validate", "-no-color"])
                    run(module, "mock-contracts", TERRAFORM, ["test", "-no-color"])
            if args.only in [None, "lint"]:
                run(
                    modules[0],
                    "lint-init",
                    TFLINT,
                    ["--init", "--config=/work/tflint.hcl"],
                    network="bridge",
                    plugin_token=True,
                )
                for module in modules:
                    run(module, "lint", TFLINT, ["--config=/work/tflint.hcl"])
            if args.only in [None, "security"]:
                for module in modules:
                    scan = [
                        "--cache-dir",
                        "/work/scanner-cache",
                        "config",
                        "--misconfig-scanners",
                        "terraform",
                        "--severity",
                        "HIGH,CRITICAL",
                        "--exit-code",
                        "1",
                        "--format",
                        "json",
                        ".",
                    ]
                    run(module, "security", TRIVY, scan, network="bridge")
                    if module == "ingress":
                        source = directory / module / "main.tf"
                        original = source.read_text()
                        marker = "#trivy:ignore:AVD-AWS-0053:exp:2026-12-19"
                        if original.count(marker) != 1:
                            raise ValueError(
                                "Public-ingress exception must remain singular and reviewable"
                            )
                        variants = {
                            "removed": original.replace(marker, ""),
                            "expired": original.replace(
                                marker, marker.replace("2026-12-19", "2000-01-01")
                            ),
                            "wrong-resource": original.replace(marker, "").replace(
                                'resource "aws_lb_listener" "http" {',
                                marker + '\nresource "aws_lb_listener" "http" {',
                            ),
                        }
                        try:
                            for name, content in variants.items():
                                source.write_text(content)
                                report = json.loads(
                                    run(
                                        module,
                                        "exception-" + name,
                                        TRIVY,
                                        scan,
                                        network="bridge",
                                        expected=1,
                                    )
                                )
                                ids = {
                                    finding["ID"]
                                    for item in report.get("Results", [])
                                    for finding in item.get("Misconfigurations", [])
                                }
                                if not ids.intersection({"AVD-AWS-0053", "AWS-0053"}):
                                    raise ValueError(
                                        "Negative control must fail on the intended public ALB finding"
                                    )
                        finally:
                            source.write_text(original)
        result["status"] = "passed"
    finally:
        (evidence / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
        print("Evidence:", evidence, flush=True)


if __name__ == "__main__":
    main()

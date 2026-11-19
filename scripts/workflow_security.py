"""Offline workflow security checks using a pinned tool and YAML-only copies.

Does not load local variable files, repository secrets, AWS credentials or GitHub
API tokens. The Docker client uses an empty configuration and the local socket.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
ZIZMOR = "ghcr.io/zizmorcore/zizmor:1.32.0@sha256:3014cc8f68f663935f69b3c3e0e41f5ce102b268d97db13db0f62208747f860d"


def findings(output):
    value = json.loads(output)
    if not isinstance(value, list):
        raise ValueError("Unexpected workflow-security report format")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Also require rejection of synthetic injection and mutable-action fixtures",
    )
    args = parser.parse_args()
    os.umask(0o077)
    evidence = ROOT / "backups" / ("workflow-security-" + uuid.uuid4().hex)
    evidence.mkdir(parents=True)
    results = {"status": "failed", "image": ZIZMOR, "steps": []}
    try:
        with tempfile.TemporaryDirectory(prefix="canopy-workflow-security-") as temp:
            directory = Path(temp)
            for name in ["home", "docker", "workflows", "fixtures"]:
                (directory / name).mkdir()
            inputs = []
            for pattern in ["*.yml", "*.yaml"]:
                for source in sorted((ROOT / ".github/workflows").glob(pattern)):
                    if source.is_symlink():
                        raise ValueError("Refusing workflow symlink")
                    shutil.copyfile(source, directory / "workflows" / source.name)
                    inputs.append("/input/workflows/" + source.name)
            if not inputs:
                raise ValueError("No workflow YAMLs found")
            dependabot = ROOT / ".github/dependabot.yml"
            if not dependabot.is_file() or dependabot.is_symlink():
                raise ValueError("A regular Dependabot configuration is required")
            shutil.copyfile(dependabot, directory / "dependabot.yml")
            inputs.append("/input/dependabot.yml")
            environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory / "home")}
            base = [
                "docker",
                "--config",
                str(directory / "docker"),
                "--host",
                "unix:///var/run/docker.sock",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=bind,source={directory},target=/input,readonly",
                ZIZMOR,
                "--offline",
                "--no-ignores",
                "--format",
                "json",
                "--min-severity",
                "medium",
                "--min-confidence",
                "medium",
            ]

            def scan(label, paths, expected_rule=None):
                result = subprocess.run(
                    [*base, *paths],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                (evidence / (label + ".stderr.log")).write_text(result.stderr)
                report = findings(result.stdout)
                safe = [
                    {
                        "rule": item["ident"],
                        "severity": item["determinations"]["severity"],
                        "confidence": item["determinations"]["confidence"],
                        "locations": [
                            {
                                "file": Path(
                                    location["symbolic"]["key"]["Local"]["verbatim_path"]
                                ).name,
                                "line": location["concrete"]["location"]["start_point"]["row"] + 1,
                            }
                            for location in item.get("locations", [])
                        ],
                    }
                    for item in report
                ]
                (evidence / (label + ".json")).write_text(json.dumps(safe, indent=2) + "\n")
                results["steps"].append(
                    {
                        "name": label,
                        "exit_code": result.returncode,
                        "findings": sorted({item["ident"] for item in report}),
                    }
                )
                if expected_rule:
                    if result.returncode == 0 or expected_rule not in {
                        item["ident"] for item in report
                    }:
                        raise RuntimeError("Unsafe workflow fixture was not rejected: " + label)
                elif result.returncode != 0 or report:
                    print(json.dumps(safe, indent=2))
                    raise RuntimeError("Workflow security gate failed: " + label)
                print("CHECK: " + label + " passed", flush=True)

            scan("repository-workflows", inputs)
            if args.self_test:
                fixtures = {
                    "template-injection": """name: Unsafe injection fixture
on:
  issues:
    types: [opened]
permissions:
  contents: read
jobs:
  example:
    runs-on: ubuntu-24.04
    steps:
      - run: echo "${{ github.event.issue.title }}"
""",
                    "unpinned-uses": """name: Mutable action fixture
on: workflow_dispatch
permissions:
  contents: read
jobs:
  example:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@main
        with:
          persist-credentials: false
""",
                }
                for rule, text in fixtures.items():
                    (directory / "fixtures" / (rule + ".yml")).write_text(text)
                    scan(rule, ["/input/fixtures/" + rule + ".yml"], expected_rule=rule)
                ignored = fixtures["template-injection"].replace(
                    '- run: echo "${{ github.event.issue.title }}"',
                    '- run: | # zizmor: ignore[template-injection]\n          echo "${{ github.event.issue.title }}"',
                )
                (directory / "fixtures/ignored.yml").write_text(ignored)
                scan(
                    "ignore-bypass",
                    ["/input/fixtures/ignored.yml"],
                    expected_rule="template-injection",
                )
        results["status"] = "passed"
    finally:
        (evidence / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2), flush=True)
        print("Evidence:", evidence, flush=True)


if __name__ == "__main__":
    main()

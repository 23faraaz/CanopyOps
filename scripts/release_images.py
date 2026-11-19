"""Build/scan release artifacts without AWS; publish them only after explicit OIDC setup.

The publish subcommand intentionally uses the job's temporary AWS session. Do not run
it locally without authorization. It never prints login passwords or credential values.
"""

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from ecr_scan_gate import check as native_scan_gate
from terraform_checks import TRIVY
from checks import HADOLINT, ACTIONLINT

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "api": ("docker/cloud-api/Dockerfile", "canopy.api"),
    "worker": ("docker/ingestion/Dockerfile", "ingestion.worker"),
    "analytics": ("docker/analytics/Dockerfile", "analytics.api"),
}
REGION = "eu-west-2"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_tag(commit, run, attempt):
    if (
        not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not re.fullmatch(r"[1-9][0-9]*", run)
        or not re.fullmatch(r"[1-9][0-9]*", attempt)
    ):
        raise ValueError("Expected a full commit SHA and positive numeric run/attempt IDs")
    return f"build-{commit}-{run}-{attempt}"


def image_config(archive):
    with tarfile.open(archive) as tar:
        entries = json.load(tar.extractfile("manifest.json"))
        if len(entries) != 1:
            raise ValueError("Expected one saved image")
        member = tar.getmember(entries[0]["Config"])
        if not member.isfile() or member.size > 2 * 1024 * 1024:
            raise ValueError("Invalid saved image configuration")
        config = tar.extractfile(member).read()
        return "sha256:" + hashlib.sha256(config).hexdigest(), json.loads(config)


def verify(directory, component, commit, run, attempt, now=None):
    directory = Path(directory)
    if any(
        (directory / name).is_symlink() for name in ["manifest.json", "image.tar", "trivy.json"]
    ):
        raise ValueError("Release artifacts must not be symlinks")
    meta = json.loads((directory / "manifest.json").read_text())
    tag = release_tag(commit, run, attempt)
    if meta.get("schema") != 1 or (meta.get("component"), meta.get("commit"), meta.get("tag")) != (
        component,
        commit,
        tag,
    ):
        raise ValueError("Artifact identity does not match this workflow run")
    if meta.get("local_image") != f"canopy-release-{component}:{tag}":
        raise ValueError("Unexpected artifact image reference")
    if meta.get("scanner") != TRIVY:
        raise ValueError("Unexpected security scanner")
    created = datetime.fromisoformat(meta["created_at"])
    now = now or datetime.now(timezone.utc)
    if created.tzinfo is None or not timedelta(0) <= now - created <= timedelta(hours=6):
        raise ValueError("Release scan is future-dated or older than six hours; rerun all jobs")
    for filename, key in [("image.tar", "archive_sha256"), ("trivy.json", "scan_sha256")]:
        if (directory / filename).is_symlink() or sha256(directory / filename) != meta[key]:
            raise ValueError("Artifact checksum mismatch: " + filename)
    config_digest, config = image_config(directory / "image.tar")
    if (
        config_digest != meta["config_digest"]
        or config["architecture"] != "amd64"
        or config["os"] != "linux"
    ):
        raise ValueError("Image configuration/platform mismatch")
    if config["config"]["User"] != "10001:10001" or config["config"]["Cmd"] != [
        "python",
        "-m",
        COMPONENTS[component][1],
    ]:
        raise ValueError("Unexpected runtime user or startup command")
    if config["config"].get("Labels", {}).get("org.opencontainers.image.revision") != commit:
        raise ValueError("Image revision label does not match the workflow commit")
    report = json.loads((directory / "trivy.json").read_text())
    if not report.get("Results") or any(
        v["Severity"] in {"HIGH", "CRITICAL"}
        for result in report["Results"]
        for v in result.get("Vulnerabilities", [])
    ):
        raise ValueError("Release vulnerability gate failed or report has no evaluated results")
    if report.get("Metadata", {}).get("ImageID") != config_digest:
        raise ValueError("Scan report is not bound to this image configuration")
    return meta


class Docker:
    def __init__(self, directory, sensitive=False):
        self.directory = directory
        self.sensitive = sensitive
        (directory / "home").mkdir()
        (directory / "docker").mkdir()
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory / "home")}
        self.prefix = [
            "docker",
            "--config",
            str(directory / "docker"),
            "--host",
            "unix:///var/run/docker.sock",
        ]

    def command(self, args, env=None, input_text=None):
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=self.env if env is None else env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            # Do not echo AWS/login output, environment values or credential-bearing commands.
            if not self.sensitive:
                print(result.stdout + result.stderr, flush=True)
            raise RuntimeError("Release command failed: " + Path(str(args[0])).name)
        return result.stdout

    def run(self, *args, **kwargs):
        return self.command([*self.prefix, *map(str, args)], **kwargs)


def verify_loaded_image(docker, image, expected_config):
    # Docker's containerd store may expose a manifest ID through .Id, whereas
    # classic stores expose the config ID. Re-export when those differ.
    if docker.run("image", "inspect", image, "--format", "{{.Id}}").strip() == expected_config:
        return
    archive = docker.directory / "reload-verification.tar"
    try:
        docker.run("save", "--output", archive, image)
        digest, _ = image_config(archive)
        if digest != expected_config:
            raise ValueError("Loaded image does not match the scanned configuration")
    finally:
        archive.unlink(missing_ok=True)


def build(args):
    tag = release_tag(args.commit, args.run, args.attempt)
    output = Path(args.directory).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="canopy-release-", dir="/tmp") as temporary:
        directory = Path(temporary)
        docker = Docker(directory)
        if docker.command(["git", "rev-parse", "HEAD"]).strip() != args.commit:
            raise ValueError("Build commit must match the checked-out commit")
        docker.command(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "app",
                "Dockerfile",
                ".dockerignore",
                "docker",
                "scripts/recovery/ledger.py",
            ]
        )
        context = directory / "source"
        context.mkdir()
        names = docker.command(
            [
                "git",
                "ls-files",
                "app",
                "Dockerfile",
                ".dockerignore",
                "docker",
                "scripts/recovery/ledger.py",
            ]
        ).splitlines()
        for name in names:
            source = ROOT / name
            if source.suffix not in {
                ".py",
                ".sql",
                ".txt",
                ".html",
                ".css",
                ".js",
            } and source.name not in {"Dockerfile", ".dockerignore"}:
                continue
            if (
                any(part in {".aws", ".secrets", ".terraform", "backups"} for part in source.parts)
                or source.is_symlink()
            ):
                raise ValueError("Unsafe build input path")
            target = context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        local_image = f"canopy-release-{args.component}:{tag}"
        print("Building once: " + args.component, flush=True)
        docker.run(
            "build",
            "--platform",
            "linux/amd64",
            "--provenance=false",
            "--label",
            "org.opencontainers.image.revision=" + args.commit,
            "-f",
            context / COMPONENTS[args.component][0],
            "-t",
            local_image,
            context,
        )
        code = """import os,importlib.util,pathlib
assert os.getuid()==10001
assert not pathlib.Path('/usr/lib/libsqlite3.so.0').exists()
try: import sqlite3
except ImportError: pass
else: raise AssertionError('SQLite must not be usable in a cloud release')
for module in ['pytest','ruff','httpx']:
 assert importlib.util.find_spec(module) is None,module
"""
        code += "\n__import__('" + COMPONENTS[args.component][1] + "')\n"
        docker.run(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,size=16m",
            "--cap-drop",
            "ALL",
            local_image,
            "python",
            "-c",
            code,
        )
        if args.component == "api":
            docker.command(
                [
                    "python3",
                    str(ROOT / "scripts/verify_cloud_runtime.py"),
                    "--smoke",
                    "--image",
                    local_image,
                ]
            )
        docker.run("save", "--output", output / "image.tar", local_image)
        (directory / "cache").mkdir()
        print("Scanning exact saved artifact: " + args.component, flush=True)
        docker.run(
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--mount",
            f"type=bind,source={output},target=/artifact",
            "--mount",
            f"type=bind,source={directory / 'cache'},target=/cache",
            TRIVY,
            "image",
            "--input",
            "/artifact/image.tar",
            "--cache-dir",
            "/cache",
            "--scanners",
            "vuln",
            "--severity",
            "HIGH,CRITICAL",
            "--exit-code",
            "1",
            "--format",
            "json",
            "--output",
            "/artifact/trivy.json",
            "--no-progress",
        )
        digest, _ = image_config(output / "image.tar")
        meta = {
            "schema": 1,
            "component": args.component,
            "commit": args.commit,
            "tag": tag,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "local_image": local_image,
            "archive_sha256": sha256(output / "image.tar"),
            "scan_sha256": sha256(output / "trivy.json"),
            "config_digest": digest,
            "scanner": TRIVY,
        }
        (output / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n")
        verify(output, args.component, args.commit, args.run, args.attempt)
        docker.run("load", "--input", output / "image.tar")
        verify_loaded_image(docker, local_image, digest)
        print(
            json.dumps(
                {"component": args.component, "config_digest": digest, "artifact": str(output)}
            ),
            flush=True,
        )


def lint():
    with tempfile.TemporaryDirectory(prefix="canopy-release-lint-", dir="/tmp") as temporary:
        docker = Docker(Path(temporary))
        for recipe, _ in COMPONENTS.values():
            docker.run(
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=bind,source={ROOT / recipe},target=/Dockerfile,readonly",
                HADOLINT,
                "hadolint",
                "/Dockerfile",
            )
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            docker.run(
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=bind,source={workflow},target=/workflow.yml,readonly",
                ACTIONLINT,
                "/workflow.yml",
            )
    print("Release Dockerfile and workflow lint passed; no application image built")


def publish(args):
    """Uses authorized ephemeral credentials only; never called by local verification."""
    meta = verify(args.directory, args.component, args.commit, args.run, args.attempt)
    if (
        not re.fullmatch(r"[0-9]{12}", args.account)
        or args.region != REGION
        or not re.fullmatch(r"[a-z0-9]+([._-][a-z0-9]+)*", args.prefix)
        or len(args.prefix) > 100
    ):
        raise ValueError("Expected the approved London account configuration")
    registry = f"{args.account}.dkr.ecr.{REGION}.amazonaws.com"
    repository = args.prefix + "/" + args.component
    target = registry + "/" + repository + ":" + meta["tag"]
    with tempfile.TemporaryDirectory(prefix="canopy-publish-", dir="/tmp") as temporary:
        docker = Docker(Path(temporary), sensitive=True)
        # Only the protected job's explicit temporary session is forwarded. No
        # profile, shared credentials, host config or ambient environment is used.
        aws_environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(docker.directory / "home"),
            "AWS_ACCESS_KEY_ID": os.environ["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": os.environ["AWS_SECRET_ACCESS_KEY"],
            "AWS_SESSION_TOKEN": os.environ["AWS_SESSION_TOKEN"],
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_EC2_METADATA_DISABLED": "true",
        }

        def aws(*parts):
            return docker.command(
                ["aws", *parts, "--region", REGION, "--no-cli-pager"], env=aws_environment
            )

        details = json.loads(aws("ecr", "describe-repositories", "--repository-names", repository))[
            "repositories"
        ]
        if (
            len(details) != 1
            or details[0]["registryId"] != args.account
            or details[0]["imageTagMutability"] != "IMMUTABLE"
            or details[0]["encryptionConfiguration"]["encryptionType"] != "AES256"
            or not details[0]["imageScanningConfiguration"]["scanOnPush"]
        ):
            raise ValueError("Registry account, encryption or tag-immutability contract failed")
        docker.run("load", "--input", Path(args.directory).resolve() / "image.tar")
        verify_loaded_image(docker, meta["local_image"], meta["config_digest"])
        password = aws("ecr", "get-login-password")
        docker.run("login", "--username", "AWS", "--password-stdin", registry, input_text=password)
        (docker.directory / "docker/config.json").chmod(0o600)
        docker.run("tag", meta["local_image"], target)
        docker.run("push", target)
        response = json.loads(
            aws(
                "ecr",
                "batch-get-image",
                "--repository-name",
                repository,
                "--image-ids",
                "imageTag=" + meta["tag"],
                "--accepted-media-types",
                "application/vnd.docker.distribution.manifest.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
            )
        )
        if response.get("failures") or len(response.get("images", [])) != 1:
            raise ValueError("ECR did not return the published image")
        image = response["images"][0]
        remote = image["imageManifest"]
        digest = image["imageId"]["imageDigest"]
        if (
            "sha256:" + hashlib.sha256(remote.encode()).hexdigest() != digest
            or json.loads(remote)["config"]["digest"] != meta["config_digest"]
        ):
            raise ValueError("Published manifest does not match the scanned image configuration")
        native = native_scan_gate(
            aws,
            args.account,
            repository,
            digest,
            Path(args.receipt).with_suffix(".native-scan.json"),
        )
        receipt = {
            "component": args.component,
            "commit": args.commit,
            "tag": meta["tag"],
            "uri": registry + "/" + repository + "@" + digest,
            "digest": digest,
            "config_digest": meta["config_digest"],
            "archive_sha256": meta["archive_sha256"],
            "published_at": datetime.now(timezone.utc).isoformat(),
            "native_scan": native,
        }
        path = Path(args.receipt)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as receipt_file:
            receipt_file.write(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["lint", "build", "verify", "publish"])
    parser.add_argument("--component", choices=COMPONENTS)
    parser.add_argument("--commit")
    parser.add_argument("--run")
    parser.add_argument("--attempt")
    parser.add_argument("--directory")
    parser.add_argument("--account")
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--prefix", default="canopyops-demo")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if args.command == "lint":
        lint()
        return
    if not all([args.component, args.commit, args.run, args.attempt, args.directory]):
        parser.error("build/verify/publish require component, commit, run, attempt and directory")
    if args.command == "build":
        build(args)
    elif args.command == "verify":
        verify(args.directory, args.component, args.commit, args.run, args.attempt)
        print("Release artifact identity, freshness, configuration and scan checks passed")
    else:
        if not args.account or not args.receipt:
            parser.error(
                "publish requires --account and --receipt; it uses an explicitly authorized AWS session"
            )
        publish(args)


if __name__ == "__main__":
    main()

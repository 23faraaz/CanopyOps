from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_images import release_tag, verify, TRIVY
import wait_for_ci
import release_images

COMMIT = "a" * 40
NOW = datetime(2026, 11, 19, 1, tzinfo=timezone.utc)


def artifact(folder, *, user="10001:10001", vulnerability=None):
    tag = release_tag(COMMIT, "123", "1")
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "User": user,
                "Cmd": ["python", "-m", "canopy.api"],
                "Labels": {"org.opencontainers.image.revision": COMMIT},
            },
        }
    ).encode()
    digest = "sha256:" + hashlib.sha256(config).hexdigest()
    with tarfile.open(folder / "image.tar", "w") as tar:
        for name, data in [
            ("config.json", config),
            ("manifest.json", b'[{"Config":"config.json"}]'),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    report = {
        "Metadata": {"ImageID": digest},
        "Results": [{"Vulnerabilities": [vulnerability] if vulnerability else []}],
    }
    (folder / "trivy.json").write_text(json.dumps(report))
    meta = {
        "schema": 1,
        "component": "api",
        "commit": COMMIT,
        "tag": tag,
        "local_image": "canopy-release-api:" + tag,
        "created_at": NOW.isoformat(),
        "config_digest": digest,
        "scanner": TRIVY,
        "archive_sha256": hashlib.sha256((folder / "image.tar").read_bytes()).hexdigest(),
        "scan_sha256": hashlib.sha256((folder / "trivy.json").read_bytes()).hexdigest(),
    }
    (folder / "manifest.json").write_text(json.dumps(meta))


def test_verified_artifact_is_bound_to_this_run_and_scan(tmp_path):
    artifact(tmp_path)
    assert verify(tmp_path, "api", COMMIT, "123", "1", now=NOW)["commit"] == COMMIT
    with pytest.raises(ValueError, match="identity"):
        verify(tmp_path, "api", COMMIT, "123", "2", now=NOW)


def test_archive_and_report_tampering_are_rejected(tmp_path):
    artifact(tmp_path)
    with (tmp_path / "image.tar").open("ab") as file:
        file.write(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path, "api", COMMIT, "123", "1", now=NOW)


@pytest.mark.parametrize("age", [timedelta(hours=7), timedelta(seconds=-1)])
def test_stale_or_future_scan_cannot_publish(tmp_path, age):
    artifact(tmp_path)
    with pytest.raises(ValueError, match="six hours"):
        verify(tmp_path, "api", COMMIT, "123", "1", now=NOW + age)


@pytest.mark.parametrize("user,vulnerability", [("0", None), ("10001:10001", {"Severity": "HIGH"})])
def test_root_runtime_and_vulnerable_image_fail_closed(tmp_path, user, vulnerability):
    artifact(tmp_path, user=user, vulnerability=vulnerability)
    with pytest.raises(ValueError):
        verify(tmp_path, "api", COMMIT, "123", "1", now=NOW)


def test_manifest_symlink_is_rejected_before_read(tmp_path):
    (tmp_path / "manifest.json").symlink_to(tmp_path / "forbidden-missing-file")
    with pytest.raises(ValueError, match="symlinks"):
        verify(tmp_path, "api", COMMIT, "123", "1", now=NOW)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_latest_failed_ci_blocks_before_aws_credentials(monkeypatch, conclusion):
    from types import SimpleNamespace

    response = {
        "workflow_runs": [{"status": "completed", "head_sha": COMMIT, "conclusion": conclusion}]
    }
    monkeypatch.setattr(
        wait_for_ci.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    )
    with pytest.raises(RuntimeError, match="did not pass"):
        wait_for_ci.wait("23faraaz/CanopyOps", COMMIT)


def test_latest_successful_ci_allows_the_exact_commit(monkeypatch):
    from types import SimpleNamespace

    response = {
        "workflow_runs": [{"status": "completed", "head_sha": COMMIT, "conclusion": "success"}]
    }
    monkeypatch.setattr(
        wait_for_ci.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    )
    wait_for_ci.wait("23faraaz/CanopyOps", COMMIT)


@pytest.mark.parametrize("mismatch", [False, True])
def test_publisher_loads_the_scanned_archive_without_rebuilding(tmp_path, monkeypatch, mismatch):
    from types import SimpleNamespace

    artifact(tmp_path)
    meta = json.loads((tmp_path / "manifest.json").read_text())
    meta["created_at"] = datetime.now(timezone.utc).isoformat()
    (tmp_path / "manifest.json").write_text(json.dumps(meta))
    calls = []
    for name in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
        monkeypatch.setenv(name, "synthetic-session-fixture")

    class FakeDocker:
        def __init__(self, directory, sensitive=False):
            assert sensitive
            self.directory = directory
            (directory / "docker").mkdir()
            (directory / "docker/config.json").write_text("{}")

        def command(self, args, env=None):
            assert env["AWS_CONFIG_FILE"] == env["AWS_SHARED_CREDENTIALS_FILE"] == "/dev/null"
            calls.append(args[:3])
            action = args[2]
            if action == "describe-repositories":
                return json.dumps(
                    {
                        "repositories": [
                            {
                                "registryId": "123456789012",
                                "imageTagMutability": "IMMUTABLE",
                                "encryptionConfiguration": {"encryptionType": "AES256"},
                                "imageScanningConfiguration": {"scanOnPush": True},
                            }
                        ]
                    }
                )
            if action == "get-login-password":
                return "synthetic-login-fixture"
            assert action == "batch-get-image"
            manifest = json.dumps(
                {
                    "schemaVersion": 2,
                    "config": {
                        "digest": "sha256:" + "0" * 64 if mismatch else meta["config_digest"]
                    },
                }
            )
            return json.dumps(
                {
                    "images": [
                        {
                            "imageManifest": manifest,
                            "imageId": {
                                "imageDigest": "sha256:"
                                + hashlib.sha256(manifest.encode()).hexdigest()
                            },
                        }
                    ]
                }
            )

        def run(self, *args, **kwargs):
            calls.append(list(args[:2]))
            return meta["config_digest"] if args[:2] == ("image", "inspect") else ""

    monkeypatch.setattr(release_images, "Docker", FakeDocker)
    args = SimpleNamespace(
        directory=str(tmp_path),
        component="api",
        commit=COMMIT,
        run="123",
        attempt="1",
        account="123456789012",
        region="eu-west-2",
        prefix="canopyops-demo",
        receipt=str(tmp_path / "receipt.json"),
    )
    if mismatch:
        with pytest.raises(ValueError, match="Published manifest"):
            release_images.publish(args)
        assert not (tmp_path / "receipt.json").exists()
    else:
        release_images.publish(args)
        receipt = json.loads((tmp_path / "receipt.json").read_text())
        assert receipt["uri"].startswith(
            "123456789012.dkr.ecr.eu-west-2.amazonaws.com/canopyops-demo/api@sha256:"
        )
    assert not any(call[0] in {"build", "run"} for call in calls)
    assert sum(call[0] == "push" for call in calls) == 1

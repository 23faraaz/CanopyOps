from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import terraform_checks


@pytest.mark.parametrize(
    "path",
    [
        "infra/terraform.tfvars",
        "infra/bootstrap/terraform.tfvars",
        "infra/private.auto.tfvars",
        "infra/private.auto.tfvars.json",
        "infra/terraform.tfstate",
        "infra/terraform.tfstate.backup",
        "infra/.terraform/terraform.tfstate",
        "infra/bootstrap.tfplan",
        "infra/.aws/credentials",
        "infra/.env",
        "infra/.secrets/password.tf",
        "infra/.terraform/modules/download/main.tf",
        "README.md",
    ],
)
def test_terraform_source_filter_rejects_private_inputs_and_artifacts(path):
    assert not terraform_checks.allowed_source(path)


@pytest.mark.parametrize(
    "path",
    [
        "infra/main.tf",
        "infra/bootstrap/main.tf",
        "infra/.terraform.lock.hcl",
        "infra/bootstrap/tests/network_permissions.tftest.hcl",
        "infra/terraform.tfvars.example",
    ],
)
def test_terraform_source_filter_includes_explicit_public_inputs(path):
    assert terraform_checks.allowed_source(path)


def test_source_copy_separates_roots_and_never_opens_filtered_paths(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "infra/bootstrap").mkdir(parents=True)
    (root / "infra/main.tf").write_text('module "example" {}')
    (root / "infra/bootstrap/main.tf").write_text('resource "example" "bootstrap" {}')
    (root / "infra/.tflint.hcl").write_text('plugin "terraform" {}')
    # Forbidden paths deliberately do not exist: attempting to copy them would fail.
    manifest = "\n".join(
        [
            "infra/main.tf",
            "infra/bootstrap/main.tf",
            "infra/registry/main.tf",
            "infra/.tflint.hcl",
            "infra/terraform.tfvars",
            "infra/terraform.tfstate",
            "infra/.env",
        ]
    )
    monkeypatch.setattr(terraform_checks, "ROOT", root)
    monkeypatch.setattr(
        terraform_checks.Checks, "command", lambda *a, **kw: SimpleNamespace(stdout=manifest)
    )
    directory = tmp_path / "work"
    directory.mkdir()
    checks = terraform_checks.Checks("infra", directory)
    assert (checks.source / "infra/main.tf").is_file()
    assert not (checks.source / "infra/bootstrap").exists()
    assert not (checks.source / "infra/registry").exists()


def test_registry_copy_includes_its_shared_module_but_not_other_roots(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for name in ["infra/registry/main.tf", "infra/modules/ecr/main.tf", "infra/.tflint.hcl"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# public fixture")
    manifest = "\n".join(
        [
            "infra/registry/main.tf",
            "infra/modules/ecr/main.tf",
            "infra/bootstrap/main.tf",
            "infra/modules/network/main.tf",
        ]
    )
    monkeypatch.setattr(terraform_checks, "ROOT", root)
    monkeypatch.setattr(
        terraform_checks.Checks, "command", lambda *a, **kw: SimpleNamespace(stdout=manifest)
    )
    work = tmp_path / "work"
    work.mkdir()
    checks = terraform_checks.Checks("infra/registry", work)
    assert (checks.source / "infra/modules/ecr/main.tf").is_file()
    assert not (checks.source / "infra/bootstrap").exists()
    assert not (checks.source / "infra/modules/network").exists()


def test_source_symlink_is_rejected_before_copy(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "infra").mkdir(parents=True)
    (root / "infra/main.tf").symlink_to(tmp_path / "forbidden-missing-target")
    monkeypatch.setattr(terraform_checks, "ROOT", root)
    monkeypatch.setattr(
        terraform_checks.Checks, "command", lambda *a, **kw: SimpleNamespace(stdout="infra/main.tf")
    )
    directory = tmp_path / "work"
    directory.mkdir()
    with pytest.raises(ValueError, match="source symlink"):
        terraform_checks.Checks("infra", directory)


def test_lint_token_reaches_installer_only(tmp_path, monkeypatch):
    checks = object.__new__(terraform_checks.Checks)
    checks.root = "infra/bootstrap"
    calls = []
    monkeypatch.setattr(checks, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    checks.lint(github_token="synthetic-test-token")
    assert calls[0][0][0] == "lint-init"
    assert calls[0][1]["github_token"] == "synthetic-test-token"
    assert calls[0][1]["network"] == "bridge"
    assert all("github_token" not in kwargs and "network" not in kwargs for _, kwargs in calls[1:])


def test_installer_token_is_not_in_arguments_or_recorded_output(tmp_path, monkeypatch):
    checks = object.__new__(terraform_checks.Checks)
    checks.root = "infra/bootstrap"
    checks.directory = tmp_path
    checks.evidence = tmp_path
    checks.results = {"steps": []}
    checks.docker = ["docker"]
    checks.env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    calls = []
    token = "synthetic-test-token"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="installer output " + token)

    monkeypatch.setattr(terraform_checks.subprocess, "run", fake_run)
    checks.run("installer", "pinned-image", ["--init"], github_token=token)
    args, kwargs = calls[0]
    assert token not in args
    assert ["--env", "GITHUB_TOKEN"] == args[-4:-2]
    assert kwargs["env"]["GITHUB_TOKEN"] == token
    assert token not in (tmp_path / "installer.log").read_text()
    assert "[REDACTED]" in (tmp_path / "installer.log").read_text()
    checks.run("offline-lint", "pinned-image", [])
    assert "GITHUB_TOKEN" not in calls[1][1]["env"]
    assert calls[1][0][calls[1][0].index("--network") + 1] == "none"

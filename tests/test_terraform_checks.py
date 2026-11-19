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

mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/test-operator"
      user_id    = "test-operator"
    }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_s3_bucket" {
    defaults        = { arn = "arn:aws:s3:::canopyops-mock-state" }
    override_during = plan
  }
}

variables {
  project_name                      = "canopyops"
  environment                       = "demo"
  aws_region                        = "eu-west-2"
  state_bucket_name                 = "canopyops-mock-state"
  terraform_operator_arn            = "arn:aws:iam::123456789012:user/test-operator"
  enable_github_publishing          = true
  existing_github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
}

run "publisher_boundaries" {
  command = plan
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0 && length(aws_iam_role.github_publisher) == 1
    error_message = "Reuse an existing account-wide provider instead of creating a duplicate."
  }
  assert {
    condition = jsondecode(aws_iam_role.github_publisher[0].assume_role_policy).Statement[0].Condition.StringEquals == {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = "repo:23faraaz/CanopyOps:environment:ecr-publish"
    }
    error_message = "Trust must match the exact audience, repository and protected environment."
  }
  assert {
    condition = toset(jsondecode(aws_iam_role_policy.github_publisher[0].policy).Statement[1].Resource) == toset([
      "arn:aws:ecr:eu-west-2:123456789012:repository/canopyops-demo/api",
      "arn:aws:ecr:eu-west-2:123456789012:repository/canopyops-demo/worker",
      "arn:aws:ecr:eu-west-2:123456789012:repository/canopyops-demo/analytics"
    ])
    error_message = "Publishing must be limited to the three intended repositories."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.github_publisher[0].policy).Statement[0].Action == ["ecr:GetAuthorizationToken"] &&
      jsondecode(aws_iam_role_policy.github_publisher[0].policy).Statement[0].Resource == "*" &&
      toset(jsondecode(aws_iam_role_policy.github_publisher[0].policy).Statement[1].Action) == toset([
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeRepositories"
      ])
    )
    error_message = "Publisher must have no state, deployment, IAM, delete or repository-configuration permissions."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.registry_state.policy).Statement :
      endswith(statement.Resource, ".tflock") if contains(statement.Action, "s3:DeleteObject")
    ])
    error_message = "State deletion must stay forbidden while lock-object deletion is allowed."
  }
  assert {
    condition     = length(aws_iam_policy.registry_management.policy) <= 6144
    error_message = "Keep repository-management permissions inside the managed-policy size quota."
  }
}

run "publishing_disabled_by_default_contract" {
  command = plan
  variables {
    enable_github_publishing = false
  }
  assert {
    condition     = length(aws_iam_role.github_publisher) == 0 && length(aws_iam_role_policy.github_publisher) == 0 && length(aws_iam_openid_connect_provider.github) == 0
    error_message = "No federation identity should be enabled before explicit configuration review."
  }
}

run "create_provider_when_explicitly_enabled" {
  command = plan
  variables {
    existing_github_oidc_provider_arn = null
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1 && aws_iam_openid_connect_provider.github[0].client_id_list == toset(["sts.amazonaws.com"])
    error_message = "Only the GitHub provider with the intended audience should be created when needed."
  }
}

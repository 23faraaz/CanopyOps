mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012", arn = "arn:aws:iam::123456789012:user/test", user_id = "test" }
  }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::canopyops-mock-state" } }
}
variables {
  project_name                      = "canopyops"
  environment                       = "demo"
  aws_region                        = "eu-west-2"
  state_bucket_name                 = "canopyops-mock-state"
  terraform_operator_arn            = "arn:aws:iam::123456789012:user/test"
  enable_terraform_workflows        = true
  existing_github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
}
run "workflow_boundaries" {
  command = plan
  assert {
    condition = alltrue([for name, role in aws_iam_role.terraform_ci :
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals == {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:23faraaz@213287679/CanopyOps@1459330795:environment:terraform-${name}"
      }
    ])
    error_message = "Trust must match exact immutable repository and distinct protected environments."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_role_policy.terraform_ci_network["plan"].policy).Statement :
      alltrue([for action in statement.Action : startswith(action, "ec2:Describe")])
    ])
    error_message = "The plan role must not have infrastructure mutation permissions."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_role_policy.terraform_ci_network["destroy"].policy).Statement :
      alltrue([for action in statement.Action : startswith(action, "ec2:Describe") || contains(local.ci_destroy_actions, action)])
    ])
    error_message = "Destroy must not create infrastructure or inherit registry/bootstrap rights."
  }
  assert {
    condition = alltrue(flatten([for name, policy in aws_iam_role_policy.terraform_ci_state : [
      for statement in jsondecode(policy.policy).Statement : endswith(statement.Resource, ".tflock") if contains(statement.Action, "s3:DeleteObject")
    ]]))
    error_message = "State delete rights must remain limited to lock objects."
  }
  assert {
    condition     = !contains(jsondecode(aws_iam_role_policy.terraform_ci_state["plan"].policy).Statement[1].Action, "s3:PutObject")
    error_message = "The plan role must not write the state object."
  }
  assert {
    condition = alltrue([for name, policy in aws_iam_role_policy.terraform_ci_network :
      length(policy.policy) + length(aws_iam_role_policy.terraform_ci_state[name].policy) + length(aws_iam_role_policy.terraform_ci_plans[name].policy) <= 10240
    ])
    error_message = "Each workflow role must fit the combined inline-policy quota."
  }
  assert {
    condition = alltrue([for rule in aws_s3_bucket_lifecycle_configuration.terraform_plans[0].rule :
      one(rule.filter).prefix == "canopyops/demo/plans/"
    ])
    error_message = "Plan expiry must never cover bootstrap, registry or demo state objects."
  }
  assert {
    condition = alltrue([for name, policy in aws_iam_role_policy.terraform_ci_plans :
      jsondecode(policy.policy).Statement[0].Action == (name == "plan" ? ["s3:PutObject"] : ["s3:GetObjectVersion"])
    ])
    error_message = "Plan upload and version-specific consumption must remain separate."
  }
}
run "workflow_activation_is_opt_in" {
  command = plan
  variables { enable_terraform_workflows = false }
  assert {
    condition     = length(aws_iam_role.terraform_ci) == 0 && length(aws_s3_bucket_lifecycle_configuration.terraform_plans) == 0
    error_message = "CI identities and retention changes require explicit activation."
  }
}


run "ci_keeps_shared_provider_without_publisher" {
  command = plan
  variables {
    existing_github_oidc_provider_arn = null
    enable_github_publishing          = false
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1
    error_message = "Disabling image publication must not delete federation still required by Terraform CI."
  }
}

mock_provider "aws" {
  override_during = plan

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/test-operator"
      user_id    = "test-operator"
    }
  }

  mock_resource "aws_s3_bucket" {
    override_during = plan
    defaults = {
      arn = "arn:aws:s3:::canopyops-mock-state"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }
}

variables {
  project_name           = "canopyops"
  environment            = "demo"
  aws_region             = "eu-west-2"
  state_bucket_name      = "canopyops-mock-state"
  terraform_operator_arn = "arn:aws:iam::123456789012:user/test-operator"
}

run "network_permission_boundaries" {
  command = plan

  assert {
    condition = (
      anytrue([
        for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
        contains(statement.Action, "ec2:DescribeNetworkInterfaces") && statement.Sid == "ReadNetworkConfiguration"
      ]) &&
      alltrue([
        for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
        !contains(statement.Action, "ec2:DeleteNetworkInterface") && !contains(statement.Action, "ec2:DetachNetworkInterface")
      ])
    )
    error_message = "Subnet teardown requires ENI discovery, not broad ENI mutation permissions."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      alltrue([for action in statement.Action : startswith(action, "ec2:") && !strcontains(action, "*")])
    ])
    error_message = "Network permissions must be explicit EC2 actions, without wildcard actions or access to other services."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Resource != "*" || alltrue([for action in statement.Action : startswith(action, "ec2:Describe")])
    ])
    error_message = "Only read-only Describe actions may use a wildcard resource."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      contains([
        "arn:aws:ec2:eu-west-2:123456789012:vpc/*",
        "arn:aws:ec2:eu-west-2:123456789012:subnet/*",
        "arn:aws:ec2:eu-west-2:123456789012:internet-gateway/*",
        "arn:aws:ec2:eu-west-2:123456789012:route-table/*"
      ], statement.Resource) if statement.Resource != "*"
    ])
    error_message = "Write permissions must stay within the configured account, region and four network resource types."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Condition.StringEquals["aws:RequestedRegion"] == "eu-west-2"
    ])
    error_message = "Every statement must restrict the requested region."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Condition.StringEquals["aws:RequestTag/Project"] == "canopyops" &&
      statement.Condition.StringEquals["aws:RequestTag/Environment"] == "demo" &&
      statement.Condition.StringEquals["aws:RequestTag/ManagedBy"] == "Terraform"
      if startswith(statement.Sid, "CreateTagged")
    ])
    error_message = "New resources must require the exact ownership tags."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Condition.StringEquals["aws:ResourceTag/Project"] == "canopyops" &&
      statement.Condition.StringEquals["aws:ResourceTag/Environment"] == "demo" &&
      statement.Condition.StringEquals["aws:ResourceTag/ManagedBy"] == "Terraform"
      if startswith(statement.Sid, "Manage") || startswith(statement.Sid, "UpdateOwned")
    ])
    error_message = "Changes and teardown must require existing ownership tags."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Condition["ForAllValues:StringEquals"]["aws:TagKeys"] == ["Name"]
      if startswith(statement.Sid, "UpdateOwned")
    ])
    error_message = "Existing-resource tagging must not change or remove ownership or tier tags."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.demo_network.policy).Statement :
      statement.Condition.StringEquals["aws:ResourceTag/Tier"] == "public"
      if contains(statement.Action, "ec2:CreateRoute") || contains(statement.Action, "ec2:ReplaceRoute") || contains(statement.Action, "ec2:DeleteRoute")
    ])
    error_message = "Individual route writes must be limited to the public route table."
  }

  assert {
    condition     = length(aws_iam_role_policy.demo_network.policy) + length(aws_iam_role_policy.demo_state.policy) + length(aws_iam_role_policy.registry_state.policy) <= 10240
    error_message = "Combined role inline policies exceed the IAM 10,240-character quota."
  }
}

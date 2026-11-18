data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  network_arn_prefix = "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}"

  network_request_conditions = {
    "aws:RequestedRegion"        = var.aws_region
    "aws:RequestTag/Project"     = var.project_name
    "aws:RequestTag/Environment" = var.environment
    "aws:RequestTag/ManagedBy"   = "Terraform"
  }

  network_owner_conditions = {
    "aws:RequestedRegion"         = var.aws_region
    "aws:ResourceTag/Project"     = var.project_name
    "aws:ResourceTag/Environment" = var.environment
    "aws:ResourceTag/ManagedBy"   = "Terraform"
  }

  # The create API and allowed tag keys belong to each new resource type.
  network_resource_types = {
    Vpc = {
      resource_type = "vpc"
      create_action = "CreateVpc"
      tag_keys      = ["Name", "Project", "Environment", "ManagedBy"]
      mutable_tags  = ["Name"]
      has_tier      = false
      owned_actions = [
        "ec2:ModifyVpcAttribute", "ec2:DeleteVpc",
        "ec2:CreateSubnet", "ec2:CreateRouteTable",
        "ec2:AttachInternetGateway", "ec2:DetachInternetGateway"
      ]
    }
    Subnet = {
      resource_type = "subnet"
      create_action = "CreateSubnet"
      tag_keys      = ["Name", "Project", "Environment", "ManagedBy", "Tier"]
      mutable_tags  = ["Name"]
      has_tier      = true
      owned_actions = [
        "ec2:ModifySubnetAttribute", "ec2:DeleteSubnet",
        "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
        "ec2:ReplaceRouteTableAssociation"
      ]
    }
    InternetGateway = {
      resource_type = "internet-gateway"
      create_action = "CreateInternetGateway"
      tag_keys      = ["Name", "Project", "Environment", "ManagedBy"]
      mutable_tags  = ["Name"]
      has_tier      = false
      owned_actions = [
        "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
        "ec2:DeleteInternetGateway"
      ]
    }
    RouteTable = {
      resource_type = "route-table"
      create_action = "CreateRouteTable"
      tag_keys      = ["Name", "Project", "Environment", "ManagedBy", "Tier"]
      mutable_tags  = ["Name"]
      has_tier      = true
      owned_actions = [
        "ec2:DeleteRouteTable", "ec2:AssociateRouteTable",
        "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation"
      ]
    }
  }
}

resource "aws_iam_role_policy" "demo_network" {
  name = "demo-network-access"
  role = aws_iam_role.terraform_demo.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid    = "ReadNetworkConfiguration"
        Effect = "Allow"
        Action = [
          "ec2:DescribeVpcs", "ec2:DescribeVpcAttribute",
          "ec2:DescribeSubnets", "ec2:DescribeRouteTables",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeInternetGateways", "ec2:DescribeAvailabilityZones",
          "ec2:DescribeTags"
        ]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      }],
      [for label, config in local.network_resource_types : {
        Sid      = "CreateTagged${label}"
        Effect   = "Allow"
        Action   = ["ec2:${config.create_action}"]
        Resource = "${local.network_arn_prefix}:${config.resource_type}/*"
        Condition = {
          StringEquals = merge(
            local.network_request_conditions,
            config.has_tier ? { "aws:RequestTag/Tier" = ["public", "app", "data"] } : {}
          )
          "ForAllValues:StringEquals" = { "aws:TagKeys" = config.tag_keys }
        }
      }],
      [for label, config in local.network_resource_types : {
        Sid      = "Tag${label}DuringCreation"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags"]
        Resource = "${local.network_arn_prefix}:${config.resource_type}/*"
        Condition = {
          StringEquals = merge(
            local.network_request_conditions,
            { "ec2:CreateAction" = config.create_action }
          )
          "ForAllValues:StringEquals" = { "aws:TagKeys" = config.tag_keys }
        }
      }],
      [for label, config in local.network_resource_types : {
        Sid       = "ManageOwned${label}"
        Effect    = "Allow"
        Action    = config.owned_actions
        Resource  = "${local.network_arn_prefix}:${config.resource_type}/*"
        Condition = { StringEquals = local.network_owner_conditions }
      }],
      [for label, config in local.network_resource_types : {
        Sid      = "UpdateOwned${label}Name"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags", "ec2:DeleteTags"]
        Resource = "${local.network_arn_prefix}:${config.resource_type}/*"
        Condition = {
          StringEquals                = local.network_owner_conditions
          "ForAllValues:StringEquals" = { "aws:TagKeys" = config.mutable_tags }
          Null                        = { "aws:TagKeys" = "false" }
        }
      }],
      [{
        Sid      = "ManagePublicRoutesOnly"
        Effect   = "Allow"
        Action   = ["ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute"]
        Resource = "${local.network_arn_prefix}:route-table/*"
        Condition = {
          StringEquals = merge(
            local.network_owner_conditions,
            { "aws:ResourceTag/Tier" = "public" }
          )
        }
      }]
    )
  })
}

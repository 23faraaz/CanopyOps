# Base networking belongs to the same demo state as the application.
# New resources require ownership tags; existing resources require matching tags.
locals {
  base_network_arn = "arn:aws:ec2:${var.aws_region}:${var.account_id}"
  base_network_request = {
    "aws:RequestedRegion"        = var.aws_region
    "aws:RequestTag/Project"     = var.project_name
    "aws:RequestTag/Environment" = var.environment
    "aws:RequestTag/ManagedBy"   = "Terraform"
  }
  base_network_owner = {
    "aws:RequestedRegion"         = var.aws_region
    "aws:ResourceTag/Project"     = var.project_name
    "aws:ResourceTag/Environment" = var.environment
    "aws:ResourceTag/ManagedBy"   = "Terraform"
  }
  base_network_types = {
    Vpc = {
      kind    = "vpc", create = "CreateVpc"
      actions = ["ec2:ModifyVpcAttribute", "ec2:DeleteVpc", "ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway"]
    }
    Subnet = {
      kind    = "subnet", create = "CreateSubnet"
      actions = ["ec2:ModifySubnetAttribute", "ec2:DeleteSubnet", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation"]
    }
    InternetGateway = {
      kind    = "internet-gateway", create = "CreateInternetGateway"
      actions = ["ec2:AttachInternetGateway", "ec2:DetachInternetGateway", "ec2:DeleteInternetGateway"]
    }
    RouteTable = {
      kind    = "route-table", create = "CreateRouteTable"
      actions = ["ec2:DeleteRouteTable", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation"]
    }
  }
  base_network_statements = concat(
    [for label, item in local.base_network_types : {
      Sid      = "CreateTagged${label}", Effect = "Allow", Action = ["ec2:${item.create}"]
      Resource = "${local.base_network_arn}:${item.kind}/*"
      Condition = { StringEquals = merge(local.base_network_request,
      contains(["Subnet", "RouteTable"], label) ? { "aws:RequestTag/Tier" = ["public", "app", "data"] } : {}) }
    }],
    [for label, item in local.base_network_types : {
      Sid       = "ManageOwned${label}", Effect = "Allow", Action = item.actions
      Resource  = "${local.base_network_arn}:${item.kind}/*"
      Condition = { StringEquals = local.base_network_owner }
    }],
    [{
      Sid      = "TagBaseNetworkDuringCreation", Effect = "Allow", Action = ["ec2:CreateTags"]
      Resource = [for item in values(local.base_network_types) : "${local.base_network_arn}:${item.kind}/*"]
      Condition = {
        StringEquals                = merge(local.base_network_request, { "ec2:CreateAction" = [for item in values(local.base_network_types) : item.create] })
        "ForAllValues:StringEquals" = { "aws:TagKeys" = ["Name", "Project", "Environment", "ManagedBy", "Tier"] }
      }
      }, {
      Sid      = "UpdateOwnedNetworkNames", Effect = "Allow", Action = ["ec2:CreateTags", "ec2:DeleteTags"]
      Resource = [for item in values(local.base_network_types) : "${local.base_network_arn}:${item.kind}/*"]
      Condition = {
        StringEquals                = local.base_network_owner
        "ForAllValues:StringEquals" = { "aws:TagKeys" = ["Name"] }
        Null                        = { "aws:TagKeys" = "false" }
      }
      }, {
      Sid       = "ManageOwnedPublicRoutes", Effect = "Allow", Action = ["ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute"]
      Resource  = "${local.base_network_arn}:route-table/*"
      Condition = { StringEquals = merge(local.base_network_owner, { "aws:ResourceTag/Tier" = "public" }) }
    }]
  )
}

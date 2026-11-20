locals {
  statements = {
    "read_infrastructure" : [
      {
        "Sid" : "GeneratorTaskInventory",
        "Effect" : "Allow",
        "Action" : ["ecs:ListTasks"],
        "Resource" : "*",
        "Condition" : {
          "StringEquals" : { "aws:RequestedRegion" : var.aws_region },
          "ArnEquals" : { "ecs:cluster" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:cluster/${var.name_prefix}" }
        }
      },
      {
        "Sid" : "RegionalMetadata",
        "Effect" : "Allow",
        "Action" : [
          "ec2:DescribeVpcEndpoints",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSecurityGroupRules",
          "ec2:DescribePrefixLists",
          "ecs:DescribeTaskDefinition",
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeLoadBalancerAttributes",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:DescribeListenerAttributes",
          "elasticloadbalancing:DescribeRules",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetGroupAttributes",
          "elasticloadbalancing:DescribeTags",
          "logs:DescribeLogGroups",
          "logs:DescribeResourcePolicies",
          "logs:ListTagsForResource",
          "logs:GetLogDelivery",
          "logs:ListLogDeliveries"
        ],
        "Resource" : "*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "RuntimeRoleMetadata",
        "Effect" : "Allow",
        "Action" : [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ]
      },
      {
        "Sid" : "BoundaryMetadata",
        "Effect" : "Allow",
        "Action" : [
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions"
        ],
        "Resource" : var.boundary_arn
      },
      {
        "Sid" : "ScopedEcsMetadata",
        "Effect" : "Allow",
        "Action" : [
          "ecs:DescribeClusters",
          "ecs:DescribeServices",
          "ecs:ListTagsForResource"
        ],
        "Resource" : [
          "arn:aws:ecs:${var.aws_region}:${var.account_id}:cluster/${var.name_prefix}",
          "arn:aws:ecs:${var.aws_region}:${var.account_id}:service/${var.name_prefix}/${var.name_prefix}-*",
          "arn:aws:ecs:${var.aws_region}:${var.account_id}:task-definition/${var.name_prefix}-*:*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ScopedRdsMetadata",
        "Effect" : "Allow",
        "Action" : [
          "rds:DescribeDBInstances",
          "rds:DescribeDBSubnetGroups",
          "rds:DescribeDBParameterGroups",
          "rds:DescribeDBParameters",
          "rds:DescribeDBSnapshots",
          "rds:ListTagsForResource"
        ],
        "Resource" : [
          "arn:aws:rds:${var.aws_region}:${var.account_id}:db:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:subgrp:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:pg:${var.name_prefix}-postgres-*",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:snapshot:${var.name_prefix}-*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ScopedElasticacheMetadata",
        "Effect" : "Allow",
        "Action" : [
          "elasticache:DescribeReplicationGroups",
          "elasticache:DescribeCacheClusters",
          "elasticache:DescribeCacheSubnetGroups",
          "elasticache:DescribeUserGroups",
          "elasticache:ListTagsForResource"
        ],
        "Resource" : [
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:replicationgroup:${var.name_prefix}-redis",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:cluster:${var.name_prefix}-redis-*",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:subnetgroup:${var.name_prefix}-redis",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:usergroup:${var.name_prefix}-analytics",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:user:${var.name_prefix}-*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      }
    ],
    "read_bucket_metadata" : [
      {
        "Sid" : "BucketConfigurationOnly",
        "Effect" : "Allow",
        "Action" : [
          "s3:GetBucketLocation",
          "s3:GetBucketPolicy",
          "s3:GetBucketPolicyStatus",
          "s3:GetBucketPublicAccessBlock",
          "s3:GetBucketVersioning",
          "s3:GetEncryptionConfiguration",
          "s3:GetBucketOwnershipControls",
          "s3:GetLifecycleConfiguration",
          "s3:GetBucketTagging",
          "s3:GetBucketAcl",
          "s3:GetBucketCORS",
          "s3:GetBucketWebsite",
          "s3:GetAccelerateConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketRequestPayment",
          "s3:GetReplicationConfiguration",
          "s3:ListBucket"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.name_prefix}-alb-logs-${var.account_id}",
          "arn:aws:s3:::${var.name_prefix}-outbox-${var.account_id}"
        ]
      },
      {
        "Sid" : "ApplicationQueueMetadata",
        "Effect" : "Allow",
        "Action" : [
          "sqs:GetQueueUrl",
          "sqs:GetQueueAttributes",
          "sqs:ListQueueTags"
        ],
        "Resource" : [
          "arn:aws:sqs:${var.aws_region}:${var.account_id}:${var.name_prefix}-readings",
          "arn:aws:sqs:${var.aws_region}:${var.account_id}:${var.name_prefix}-readings-dlq"
        ]
      }
    ],
    "read_security" : [
      {
        "Sid" : "ApplicationSecretMetadata",
        "Effect" : "Allow",
        "Action" : [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetResourcePolicy"
        ],
        "Resource" : [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/api-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/worker-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/analytics-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/redis-??????"
        ]
      },
      {
        "Sid" : "OwnedKeyMetadata",
        "Effect" : "Allow",
        "Action" : [
          "kms:DescribeKey",
          "kms:GetKeyPolicy",
          "kms:ListResourceTags"
        ],
        "Resource" : "arn:aws:kms:${var.aws_region}:${var.account_id}:key/*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform",
            "aws:ResourceTag/Name" : [
              "${var.name_prefix}-logs",
              "${var.name_prefix}-outbox"
            ]
          }
        }
      },
      {
        "Sid" : "KeyAliasMetadata",
        "Effect" : "Allow",
        "Action" : [
          "kms:ListAliases"
        ],
        "Resource" : "*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ZoneMetadata",
        "Effect" : "Allow",
        "Action" : [
          "route53:GetHostedZone",
          "route53:ListResourceRecordSets"
        ],
        "Resource" : "arn:aws:route53:::hostedzone/${var.hosted_zone_id}"
      },
      {
        "Sid" : "DnsChangeStatus",
        "Effect" : "Allow",
        "Action" : [
          "route53:GetChange"
        ],
        "Resource" : "arn:aws:route53:::change/*"
      },
      {
        "Sid" : "CertificateMetadata",
        "Effect" : "Allow",
        "Action" : [
          "acm:DescribeCertificate",
          "acm:ListTagsForCertificate"
        ],
        "Resource" : var.certificate_arn,
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ApplicationWafMetadata",
        "Effect" : "Allow",
        "Action" : [
          "wafv2:GetWebACL",
          "wafv2:GetLoggingConfiguration",
          "wafv2:ListTagsForResource",
          "wafv2:GetWebACLForResource"
        ],
        "Resource" : [
          "arn:aws:wafv2:${var.aws_region}:${var.account_id}:regional/webacl/${var.name_prefix}-web/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:loadbalancer/app/${var.name_prefix}-alb/*"
        ]
      },
      {
        "Sid" : "ProtectRuntimeBoundary",
        "Effect" : "Deny",
        "Action" : [
          "iam:CreatePolicyVersion",
          "iam:SetDefaultPolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:DeletePolicy"
        ],
        "Resource" : var.boundary_arn
      },
      {
        "Sid" : "NeverRemoveRoleBoundary",
        "Effect" : "Deny",
        "Action" : [
          "iam:DeleteRolePermissionsBoundary"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ]
      },
      {
        "Sid" : "NeverReadOrPublishCredentialValues",
        "Effect" : "Deny",
        "Action" : [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecretVersionStage",
          "secretsmanager:UpdateSecret"
        ],
        "Resource" : [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/api-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/worker-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/analytics-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/redis-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:rds!db-*"
        ]
      },
      {
        "Sid" : "ManagedSecretKeyDescription",
        "Effect" : "Allow",
        "Action" : [
          "kms:DescribeKey"
        ],
        "Resource" : "arn:aws:kms:${var.aws_region}:${var.account_id}:key/*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      }
    ],
    "network" : [
      {
        "Sid" : "CreateTaggedGroupsAndEndpoints",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateSecurityGroup",
          "ec2:CreateVpcEndpoint"
        ],
        "Resource" : [
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc-endpoint/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "OwnedNetworkReferences",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateSecurityGroup",
          "ec2:CreateVpcEndpoint"
        ],
        "Resource" : [
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:subnet/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:route-table/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ManageOwnedGroupsAndEndpoints",
        "Effect" : "Allow",
        "Action" : [
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:ModifySecurityGroupRules",
          "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
          "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
          "ec2:DeleteVpcEndpoints",
          "ec2:ModifyVpcEndpoint"
        ],
        "Resource" : [
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group-rule/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc-endpoint/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "TagOnlyOnApplicationCreation",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateTags"
        ],
        "Resource" : [
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group-rule/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc-endpoint/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "ec2:CreateAction" : [
              "CreateSecurityGroup",
              "CreateVpcEndpoint",
              "AuthorizeSecurityGroupIngress",
              "AuthorizeSecurityGroupEgress"
            ]
          }
        }
      },
      {
        "Sid" : "UpdateOwnedNetworkTags",
        "Effect" : "Allow",
        "Action" : [
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ],
        "Resource" : [
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group-rule/*",
          "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc-endpoint/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform"
          }
        }
      }
    ],
    "compute" : [
      {
        "Sid" : "CreateTaggedCluster",
        "Effect" : "Allow",
        "Action" : [
          "ecs:CreateCluster"
        ],
        "Resource" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:cluster/${var.name_prefix}",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ManageApplicationCluster",
        "Effect" : "Allow",
        "Action" : [
          "ecs:UpdateCluster",
          "ecs:UpdateClusterSettings",
          "ecs:TagResource",
          "ecs:UntagResource"
        ],
        "Resource" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:cluster/${var.name_prefix}",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "CreateApplicationServices",
        "Effect" : "Allow",
        "Action" : [
          "ecs:CreateService"
        ],
        "Resource" : [
          "arn:aws:ecs:${var.aws_region}:${var.account_id}:service/${var.name_prefix}/${var.name_prefix}-*",
          "arn:aws:ecs:${var.aws_region}:${var.account_id}:cluster/${var.name_prefix}"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ManageApplicationServices",
        "Effect" : "Allow",
        "Action" : [
          "ecs:UpdateService",
          "ecs:DeleteService",
          "ecs:TagResource",
          "ecs:UntagResource"
        ],
        "Resource" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:service/${var.name_prefix}/${var.name_prefix}-*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "RegisterTaggedTaskDefinitions",
        "Effect" : "Allow",
        "Action" : [
          "ecs:RegisterTaskDefinition"
        ],
        "Resource" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:task-definition/${var.name_prefix}-*:*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform",
            "ecs:privileged" : "false"
          },
          "ForAllValues:StringEquals" : {
            "ecs:compute-compatibility" : [
              "FARGATE"
            ]
          },
          "NumericLessThanEquals" : {
            "ecs:task-cpu" : "512",
            "ecs:task-memory" : "1024"
          }
        }
      },
      {
        "Sid" : "ManageApplicationTaskDefinitions",
        "Effect" : "Allow",
        "Action" : [
          "ecs:TagResource",
          "ecs:UntagResource"
        ],
        "Resource" : "arn:aws:ecs:${var.aws_region}:${var.account_id}:task-definition/${var.name_prefix}-*:*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      }
    ],
    "data" : [
      {
        "Sid" : "CreateDatabaseResources",
        "Effect" : "Allow",
        "Action" : [
          "rds:CreateDBInstance",
          "rds:CreateDBSubnetGroup",
          "rds:CreateDBParameterGroup"
        ],
        "Resource" : [
          "arn:aws:rds:${var.aws_region}:${var.account_id}:db:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:subgrp:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:pg:${var.name_prefix}-postgres-*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ManageDatabaseResources",
        "Effect" : "Allow",
        "Action" : [
          "rds:ModifyDBInstance",
          "rds:DeleteDBInstance",
          "rds:ModifyDBSubnetGroup",
          "rds:DeleteDBSubnetGroup",
          "rds:ModifyDBParameterGroup",
          "rds:DeleteDBParameterGroup",
          "rds:AddTagsToResource",
          "rds:RemoveTagsFromResource"
        ],
        "Resource" : [
          "arn:aws:rds:${var.aws_region}:${var.account_id}:db:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:subgrp:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:pg:${var.name_prefix}-postgres-*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "FinalDatabaseSnapshots",
        "Effect" : "Allow",
        "Action" : [
          "rds:CreateDBSnapshot",
          "rds:AddTagsToResource"
        ],
        "Resource" : [
          "arn:aws:rds:${var.aws_region}:${var.account_id}:db:${var.name_prefix}-postgres",
          "arn:aws:rds:${var.aws_region}:${var.account_id}:snapshot:${var.name_prefix}-*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ManagePrivateCache",
        "Effect" : "Allow",
        "Action" : [
          "elasticache:CreateReplicationGroup",
          "elasticache:ModifyReplicationGroup",
          "elasticache:DeleteReplicationGroup",
          "elasticache:CreateCacheSubnetGroup",
          "elasticache:ModifyCacheSubnetGroup",
          "elasticache:DeleteCacheSubnetGroup",
          "elasticache:CreateUserGroup",
          "elasticache:ModifyUserGroup",
          "elasticache:DeleteUserGroup",
          "elasticache:AddTagsToResource",
          "elasticache:RemoveTagsFromResource"
        ],
        "Resource" : [
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:replicationgroup:${var.name_prefix}-redis",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:subnetgroup:${var.name_prefix}-redis",
          "arn:aws:elasticache:${var.aws_region}:${var.account_id}:usergroup:${var.name_prefix}-analytics"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ReferenceProvisionedRedisUsers",
        "Effect" : "Allow",
        "Action" : [
          "elasticache:DescribeUsers"
        ],
        "Resource" : "arn:aws:elasticache:${var.aws_region}:${var.account_id}:user:${var.name_prefix}-*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ManageIngestionQueues",
        "Effect" : "Allow",
        "Action" : [
          "sqs:CreateQueue",
          "sqs:SetQueueAttributes",
          "sqs:DeleteQueue",
          "sqs:TagQueue",
          "sqs:UntagQueue"
        ],
        "Resource" : [
          "arn:aws:sqs:${var.aws_region}:${var.account_id}:${var.name_prefix}-readings",
          "arn:aws:sqs:${var.aws_region}:${var.account_id}:${var.name_prefix}-readings-dlq"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "CreateRuntimeSecretMetadata",
        "Effect" : "Allow",
        "Action" : [
          "secretsmanager:CreateSecret"
        ],
        "Resource" : [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/api-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/worker-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/analytics-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/redis-??????"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ManageRuntimeSecretMetadata",
        "Effect" : "Allow",
        "Action" : [
          "secretsmanager:RestoreSecret",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource"
        ],
        "Resource" : [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/api-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/worker-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/analytics-??????",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/redis-??????"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "RdsManagedSecretCreation",
        "Effect" : "Allow",
        "Action" : [
          "secretsmanager:CreateSecret",
          "secretsmanager:TagResource"
        ],
        "Resource" : "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:rds!db-*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          },
          "ForAnyValue:StringEquals" : {
            "aws:CalledVia" : [
              "rds.amazonaws.com"
            ]
          }
        }
      }
    ],
    "edge" : [
      {
        "Sid" : "ManageApplicationIngress",
        "Effect" : "Allow",
        "Action" : [
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:SetSecurityGroups",
          "elasticloadbalancing:SetSubnets",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:CreateListener",
          "elasticloadbalancing:ModifyListener",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:SetRulePriorities",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags"
        ],
        "Resource" : [
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:loadbalancer/app/${var.name_prefix}-alb/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:targetgroup/${var.name_prefix}-api/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:targetgroup/${var.name_prefix}-analytics/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:listener/app/${var.name_prefix}-alb/*/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:listener-rule/app/${var.name_prefix}-alb/*/*/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ChangeOnlyApplicationDns",
        "Effect" : "Allow",
        "Action" : [
          "route53:ChangeResourceRecordSets"
        ],
        "Resource" : "arn:aws:route53:::hostedzone/${var.hosted_zone_id}",
        "Condition" : {
          "ForAllValues:StringEquals" : {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames" : [
              var.domain_name,
              "analytics.${var.domain_name}"
            ],
            "route53:ChangeResourceRecordSetsRecordTypes" : [
              "A"
            ],
            "route53:ChangeResourceRecordSetsActions" : var.mode == "destroy" ? ["DELETE"] : ["CREATE", "UPSERT", "DELETE"]
          }
        }
      },
      {
        "Sid" : "CreateTaggedRegionalWaf",
        "Effect" : "Allow",
        "Action" : [
          "wafv2:CreateWebACL"
        ],
        "Resource" : "arn:aws:wafv2:${var.aws_region}:${var.account_id}:regional/webacl/${var.name_prefix}-web/*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "ManageApplicationWaf",
        "Effect" : "Allow",
        "Action" : [
          "wafv2:UpdateWebACL",
          "wafv2:DeleteWebACL",
          "wafv2:AssociateWebACL",
          "wafv2:DisassociateWebACL",
          "wafv2:PutLoggingConfiguration",
          "wafv2:DeleteLoggingConfiguration",
          "wafv2:TagResource",
          "wafv2:UntagResource"
        ],
        "Resource" : [
          "arn:aws:wafv2:${var.aws_region}:${var.account_id}:regional/webacl/${var.name_prefix}-web/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${var.account_id}:loadbalancer/app/${var.name_prefix}-alb/*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "WafAwsLogDeliveryApi",
        "Effect" : "Allow",
        "Action" : [
          "logs:CreateLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:PutResourcePolicy"
        ],
        "Resource" : "*",
        "Condition" : {
          "ForAnyValue:StringEquals" : { "aws:CalledVia" : ["wafv2.amazonaws.com"] },
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      }
    ],
    "storage" : [
      {
        "Sid" : "DeleteDisposableLogGroups",
        "Effect" : "Allow",
        "Action" : ["logs:DeleteLogGroup"],
        "Resource" : [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-api",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-api:*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-worker",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-worker:*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-analytics",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-analytics:*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/rds/instance/${var.name_prefix}-postgres/*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:aws-waf-logs-${var.name_prefix}",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:aws-waf-logs-${var.name_prefix}:*"
        ],
        "Condition" : { "StringEquals" : { "aws:RequestedRegion" : var.aws_region } }
      },
      {
        "Sid" : "ManageApplicationLogGroups",
        "Effect" : "Allow",
        "Action" : [
          "logs:CreateLogGroup",
          "logs:PutRetentionPolicy",
          "logs:AssociateKmsKey",
          "logs:DisassociateKmsKey",
          "logs:TagResource",
          "logs:UntagResource"
        ],
        "Resource" : [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/rds/instance/${var.name_prefix}-postgres/*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:aws-waf-logs-${var.name_prefix}",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:aws-waf-logs-${var.name_prefix}:*"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "CreateTaggedLogAndOutboxKeys",
        "Effect" : "Allow",
        "Action" : [
          "kms:CreateKey"
        ],
        "Resource" : "*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "MaintainOwnedKeys",
        "Effect" : "Allow",
        "Action" : [
          "kms:PutKeyPolicy",
          "kms:EnableKeyRotation",
          "kms:DisableKeyRotation",
          "kms:TagResource",
          "kms:UntagResource",
          "kms:GetKeyRotationStatus"
        ],
        "Resource" : "arn:aws:kms:${var.aws_region}:${var.account_id}:key/*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform",
            "aws:ResourceTag/Name" : [
              "${var.name_prefix}-logs",
              "${var.name_prefix}-outbox"
            ]
          }
        }
      },
      {
        "Sid" : "CreateApplicationLogAlias",
        "Effect" : "Allow",
        "Action" : [
          "kms:CreateAlias",
          "kms:UpdateAlias"
        ],
        "Resource" : [
          "arn:aws:kms:${var.aws_region}:${var.account_id}:alias/${var.name_prefix}-logs"
        ],
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region
          }
        }
      },
      {
        "Sid" : "AliasOnlyOwnedLogKey",
        "Effect" : "Allow",
        "Action" : ["kms:CreateAlias", "kms:UpdateAlias"],
        "Resource" : "arn:aws:kms:${var.aws_region}:${var.account_id}:key/*",
        "Condition" : {
          "StringEquals" : {
            "aws:RequestedRegion" : var.aws_region,
            "aws:ResourceTag/Project" : var.project_name,
            "aws:ResourceTag/Environment" : var.environment,
            "aws:ResourceTag/ManagedBy" : "Terraform",
            "aws:ResourceTag/Name" : "${var.name_prefix}-logs"
          }
        }
      },
      {
        "Sid" : "CreateOnlyApplicationBuckets",
        "Effect" : "Allow",
        "Action" : [
          "s3:CreateBucket"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.name_prefix}-alb-logs-${var.account_id}",
          "arn:aws:s3:::${var.name_prefix}-outbox-${var.account_id}"
        ],
        "Condition" : {
          "StringEquals" : {
            "s3:LocationConstraint" : var.aws_region
          }
        }
      },
      {
        "Sid" : "ConfigureOnlyApplicationBuckets",
        "Effect" : "Allow",
        "Action" : [
          "s3:PutBucketPolicy",
          "s3:DeleteBucketPolicy",
          "s3:PutBucketPublicAccessBlock",
          "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration",
          "s3:PutBucketOwnershipControls",
          "s3:PutLifecycleConfiguration",
          "s3:PutBucketTagging"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.name_prefix}-alb-logs-${var.account_id}",
          "arn:aws:s3:::${var.name_prefix}-outbox-${var.account_id}"
        ]
      }
    ],
    "identity" : [
      {
        "Sid" : "CreateBoundedRuntimeRoles",
        "Effect" : "Allow",
        "Action" : [
          "iam:CreateRole"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ],
        "Condition" : {
          "StringEquals" : {
            "iam:PermissionsBoundary" : var.boundary_arn,
            "aws:RequestTag/Project" : var.project_name,
            "aws:RequestTag/Environment" : var.environment,
            "aws:RequestTag/ManagedBy" : "Terraform"
          }
        }
      },
      {
        "Sid" : "MaintainBoundedRoles",
        "Effect" : "Allow",
        "Action" : [
          "iam:UpdateRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:PutRolePolicy",
          "iam:TagRole",
          "iam:UntagRole"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ]
      },
      {
        "Sid" : "KeepExactBoundary",
        "Effect" : "Allow",
        "Action" : [
          "iam:PutRolePermissionsBoundary"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ],
        "Condition" : {
          "StringEquals" : {
            "iam:PermissionsBoundary" : var.boundary_arn
          }
        }
      },
      {
        "Sid" : "PassOnlyRuntimeRolesToEcs",
        "Effect" : "Allow",
        "Action" : [
          "iam:PassRole"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-api-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-worker-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-analytics-task",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-execution",
          "arn:aws:iam::${var.account_id}:role/${var.name_prefix}-generator-task"
        ],
        "Condition" : {
          "StringEquals" : {
            "iam:PassedToService" : "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        "Sid" : "RequiredAwsServiceLinkedRoles",
        "Effect" : "Allow",
        "Action" : [
          "iam:CreateServiceLinkedRole"
        ],
        "Resource" : [
          "arn:aws:iam::${var.account_id}:role/aws-service-role/ecs.amazonaws.com/AWSServiceRoleFor*",
          "arn:aws:iam::${var.account_id}:role/aws-service-role/rds.amazonaws.com/AWSServiceRoleFor*",
          "arn:aws:iam::${var.account_id}:role/aws-service-role/elasticloadbalancing.amazonaws.com/AWSServiceRoleFor*",
          "arn:aws:iam::${var.account_id}:role/aws-service-role/elasticache.amazonaws.com/AWSServiceRoleFor*",
          "arn:aws:iam::${var.account_id}:role/aws-service-role/wafv2.amazonaws.com/AWSServiceRoleFor*"
        ],
        "Condition" : {
          "StringEquals" : {
            "iam:AWSServiceName" : [
              "ecs.amazonaws.com",
              "rds.amazonaws.com",
              "elasticloadbalancing.amazonaws.com",
              "elasticache.amazonaws.com",
              "wafv2.amazonaws.com"
            ]
          }
        }
      }
    ]
  }
  destroy_actions = [
    "ec2:DeleteSecurityGroup", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:DeleteVpcEndpoints",
    "ecs:DeleteService",
    "rds:DeleteDBInstance", "rds:DeleteDBSubnetGroup", "rds:DeleteDBParameterGroup", "rds:CreateDBSnapshot", "rds:AddTagsToResource",
    "elasticache:DeleteReplicationGroup", "elasticache:DeleteCacheSubnetGroup", "elasticache:DeleteUserGroup",
    "sqs:DeleteQueue",
    "elasticloadbalancing:DeleteLoadBalancer", "elasticloadbalancing:DeleteTargetGroup", "elasticloadbalancing:DeleteListener", "elasticloadbalancing:DeleteRule",
    "route53:ChangeResourceRecordSets",
    "wafv2:DeleteWebACL", "wafv2:DisassociateWebACL", "wafv2:DeleteLoggingConfiguration",
    "logs:DeleteLogDelivery", "logs:DeleteLogGroup"
  ]
  writes = {
    for group, statements in local.statements : group => [
      for statement in statements : merge(statement, {
        Action = [for action in statement.Action : action if var.mode == "deploy" || contains(local.destroy_actions, action)]
      }) if var.mode == "deploy" || anytrue([for action in statement.Action : contains(local.destroy_actions, action)])
    ] if !startswith(group, "read_") && var.mode != "plan"
  }
  policies = merge({ for group, rows in local.statements : group => rows if startswith(group, "read_") }, { for group, rows in local.writes : group => rows if length(rows) > 0 })
}
resource "aws_iam_policy" "this" {
  for_each    = local.policies
  name        = "${var.name_prefix}-terraform-${var.mode}-${each.key}"
  description = "Reviewed ${var.mode} ${each.key} permissions; runtime secret values and retained data are excluded"
  policy      = jsonencode({ Version = "2012-10-17", Statement = each.value })
  tags        = merge(var.tags, { Name = "${var.name_prefix}-terraform-${var.mode}-${each.key}", ManagedBy = "Terraform" })
}
resource "aws_iam_role_policy_attachment" "this" {
  for_each   = aws_iam_policy.this
  role       = var.role_name
  policy_arn = each.value.arn
}

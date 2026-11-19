locals {
  # Each entry is one permitted TCP connection, not an entire subnet allowance.
  connections = merge({
    alb_to_api = {
      source = "alb", destination = "api", port = 8000
    }
    alb_to_analytics = {
      source = "alb", destination = "analytics", port = 8081
    }
    api_to_postgres = {
      source = "api", destination = "postgres", port = 5432
    }
    worker_to_postgres = {
      source = "worker", destination = "postgres", port = 5432
    }
    analytics_to_postgres = {
      source = "analytics", destination = "postgres", port = 5432
    }
    analytics_to_redis = {
      source = "analytics", destination = "redis", port = 6379
    }
    }, {
    for service in ["api", "analytics", "worker", "generator"] : "${service}_to_endpoints" => {
      source = service, destination = "endpoints", port = 443
    }
  })
}

resource "aws_vpc_security_group_ingress_rule" "connection" {
  for_each = local.connections

  security_group_id            = aws_security_group.this[each.value.destination].id
  referenced_security_group_id = aws_security_group.this[each.value.source].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = "Allow ${each.value.source} to ${each.value.destination} on TCP ${each.value.port}"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-${replace(each.key, "_", "-")}-in"
    Service   = each.value.destination
    ManagedBy = "Terraform"
  })
}

resource "aws_vpc_security_group_egress_rule" "connection" {
  for_each = local.connections

  security_group_id            = aws_security_group.this[each.value.source].id
  referenced_security_group_id = aws_security_group.this[each.value.destination].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = "Allow ${each.value.source} to ${each.value.destination} on TCP ${each.value.port}"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-${replace(each.key, "_", "-")}-out"
    Service   = each.value.source
    ManagedBy = "Terraform"
  })
}

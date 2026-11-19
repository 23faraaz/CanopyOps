mock_provider "aws" {
  override_during = plan
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["alb"]
  values          = { id = "sg-00000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["api"]
  values          = { id = "sg-00000002" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["analytics"]
  values          = { id = "sg-00000003" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["worker"]
  values          = { id = "sg-00000004" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["generator"]
  values          = { id = "sg-00000005" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["postgres"]
  values          = { id = "sg-00000006" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["redis"]
  values          = { id = "sg-00000007" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.this["endpoints"]
  values          = { id = "sg-00000008" }
}
variables {
  name_prefix = "canopyops-test"
  vpc_id      = "vpc-11111111"
  tags        = { Project = "canopyops", Environment = "test" }
}
run "paired_directional_rules" {
  command = plan
  assert {
    condition = length(aws_vpc_security_group_ingress_rule.connection) == 10 && length(aws_vpc_security_group_egress_rule.connection) == 10 && alltrue([
      for name, inbound in aws_vpc_security_group_ingress_rule.connection :
      aws_vpc_security_group_egress_rule.connection[name].security_group_id == inbound.referenced_security_group_id &&
      aws_vpc_security_group_egress_rule.connection[name].referenced_security_group_id == inbound.security_group_id &&
      aws_vpc_security_group_egress_rule.connection[name].from_port == inbound.from_port &&
      aws_vpc_security_group_egress_rule.connection[name].to_port == inbound.to_port
    ])
    error_message = "Every allowed connection needs matching source egress and destination ingress."
  }
}
run "data_access_is_scoped" {
  command = plan
  assert {
    condition     = toset([for rule in aws_vpc_security_group_ingress_rule.connection : rule.referenced_security_group_id if rule.security_group_id == aws_security_group.this["postgres"].id]) == toset([aws_security_group.this["api"].id, aws_security_group.this["worker"].id, aws_security_group.this["analytics"].id]) && alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : rule.from_port == 5432 && rule.to_port == 5432 if rule.security_group_id == aws_security_group.this["postgres"].id])
    error_message = "PostgreSQL must accept only API, worker and analytics on TCP 5432."
  }
  assert {
    condition     = length([for rule in aws_vpc_security_group_ingress_rule.connection : rule if rule.security_group_id == aws_security_group.this["redis"].id]) == 1 && alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : rule.referenced_security_group_id == aws_security_group.this["analytics"].id && rule.from_port == 6379 && rule.to_port == 6379 if rule.security_group_id == aws_security_group.this["redis"].id])
    error_message = "Redis must accept only analytics on TCP 6379."
  }
}
run "service_connections_have_no_public_or_metrics_access" {
  command = plan
  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : rule.cidr_ipv4 == null && rule.cidr_ipv6 == null && rule.prefix_list_id == null && rule.ip_protocol == "tcp" && rule.from_port == rule.to_port && rule.from_port != 9100]) && alltrue([for rule in aws_vpc_security_group_egress_rule.connection : rule.cidr_ipv4 == null && rule.cidr_ipv6 == null && rule.prefix_list_id == null && rule.ip_protocol == "tcp" && rule.from_port == rule.to_port && rule.from_port != 9100])
    error_message = "Service connections must reference SGs, use single TCP ports and leave metrics access closed."
  }
  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : !contains([aws_security_group.this["worker"].id, aws_security_group.this["generator"].id], rule.security_group_id)])
    error_message = "Worker and generator do not need inbound application listeners."
  }
}
run "generator_and_endpoint_access" {
  command = plan
  assert {
    condition     = length([for rule in aws_vpc_security_group_egress_rule.connection : rule if rule.security_group_id == aws_security_group.this["generator"].id]) == 1 && alltrue([for rule in aws_vpc_security_group_egress_rule.connection : rule.referenced_security_group_id == aws_security_group.this["endpoints"].id && rule.from_port == 443 if rule.security_group_id == aws_security_group.this["generator"].id])
    error_message = "The generator must not gain database/cache access."
  }
  assert {
    condition     = toset([for rule in aws_vpc_security_group_ingress_rule.connection : rule.referenced_security_group_id if rule.security_group_id == aws_security_group.this["endpoints"].id]) == toset([aws_security_group.this["api"].id, aws_security_group.this["analytics"].id, aws_security_group.this["worker"].id, aws_security_group.this["generator"].id]) && alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : rule.from_port == 443 && rule.to_port == 443 if rule.security_group_id == aws_security_group.this["endpoints"].id])
    error_message = "Endpoint interfaces accept HTTPS only from the four task groups."
  }
}
run "alb_reaches_only_application_ports" {
  command = plan
  assert {
    condition     = length([for rule in aws_vpc_security_group_egress_rule.connection : rule if rule.security_group_id == aws_security_group.this["alb"].id]) == 2 && alltrue([for rule in aws_vpc_security_group_egress_rule.connection : (rule.referenced_security_group_id == aws_security_group.this["api"].id && rule.from_port == 8000) || (rule.referenced_security_group_id == aws_security_group.this["analytics"].id && rule.from_port == 8081) if rule.security_group_id == aws_security_group.this["alb"].id])
    error_message = "ALB outbound rules must reach API 8000 and analytics 8081 only."
  }
}

run "public_ingress_is_only_alb_web_ports" {
  command = plan
  assert {
    condition = length(aws_vpc_security_group_ingress_rule.alb_public) == 2 && toset([for rule in aws_vpc_security_group_ingress_rule.alb_public : rule.from_port]) == toset([80, 443]) && alltrue([
      for rule in aws_vpc_security_group_ingress_rule.alb_public :
      rule.security_group_id == aws_security_group.this["alb"].id &&
      rule.cidr_ipv4 == "0.0.0.0/0" && rule.cidr_ipv6 == null &&
      rule.referenced_security_group_id == null && rule.prefix_list_id == null &&
      rule.ip_protocol == "tcp" && rule.from_port == rule.to_port
    ])
    error_message = "Public rules must target only ALB TCP 80/443, never a private service or metrics port."
  }
}
run "restrict_rehearsal_clients_without_changing_service_rules" {
  command = plan
  variables { alb_ingress_ipv4_cidr = "203.0.113.0/24" }
  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.alb_public : rule.cidr_ipv4 == "203.0.113.0/24" && rule.security_group_id == aws_security_group.this["alb"].id]) && alltrue([for rule in aws_vpc_security_group_ingress_rule.connection : rule.cidr_ipv4 == null && rule.cidr_ipv6 == null])
    error_message = "Restricting ALB clients must not add CIDR access to private services."
  }
}
run "reject_ipv6_in_ipv4_input" {
  command = plan
  variables { alb_ingress_ipv4_cidr = "::/0" }
  expect_failures = [var.alb_ingress_ipv4_cidr]
}

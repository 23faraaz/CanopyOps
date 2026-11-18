output "vpc_id" {
  description = "ID of the CanopyOps VPC"
  value       = aws_vpc.this.id
}

output "subnet_ids" {
  description = "Subnet IDs keyed by stable tier and placement slot"
  value       = { for key, subnet in aws_subnet.this : key => subnet.id }
}

output "public_subnet_ids" {
  description = "Public ALB subnet IDs keyed by placement slot"
  value = {
    for key, subnet in aws_subnet.this : key => subnet.id
    if local.subnets[key].tier == "public"
  }
}

output "private_app_subnet_ids" {
  description = "Private ECS and endpoint subnet IDs keyed by placement slot"
  value = {
    for key, subnet in aws_subnet.this : key => subnet.id
    if local.subnets[key].tier == "app"
  }
}

output "isolated_data_subnet_ids" {
  description = "Isolated RDS and Redis subnet IDs keyed by placement slot"
  value = {
    for key, subnet in aws_subnet.this : key => subnet.id
    if local.subnets[key].tier == "data"
  }
}

output "route_table_ids" {
  description = "Route table IDs keyed by public, app and data tier"
  value       = { for tier, table in aws_route_table.tier : tier => table.id }
}

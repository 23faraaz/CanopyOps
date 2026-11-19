locals {
  alb_public_ports = {
    http  = 80
    https = 443
  }
}

# Public reachability is limited to the ALB. Its HTTP listener redirects to HTTPS.
resource "aws_vpc_security_group_ingress_rule" "alb_public" {
  for_each = local.alb_public_ports

  security_group_id = aws_security_group.this["alb"].id
  cidr_ipv4         = var.alb_ingress_ipv4_cidr
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
  description       = "Allow ${each.key} requests to the public ALB"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-alb-public-${each.key}"
    Service   = "alb"
    ManagedBy = "Terraform"
  })
}

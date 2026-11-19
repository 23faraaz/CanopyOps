# Preserve the original ALB/API pair if a caller already applied those addresses.
# These declarations do not inspect or migrate any remote state by themselves.
moved {
  from = aws_vpc_security_group_ingress_rule.api_from_alb
  to   = aws_vpc_security_group_ingress_rule.connection["alb_to_api"]
}

moved {
  from = aws_vpc_security_group_egress_rule.alb_to_api
  to   = aws_vpc_security_group_egress_rule.connection["alb_to_api"]
}

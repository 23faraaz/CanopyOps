locals {
  security_groups = {
    alb       = "Public HTTPS ingress"
    api       = "Private API tasks"
    analytics = "Private analytics tasks"
    worker    = "Private ingestion workers"
    generator = "Synthetic reading producer"
    postgres  = "Private PostgreSQL database"
    redis     = "Private analytics cache"
    endpoints = "Private AWS interface endpoints"
  }
}

resource "aws_security_group" "this" {
  for_each = local.security_groups

  name        = "${var.name_prefix}-${each.key}"
  description = each.value
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-${each.key}"
    Service   = each.key
    ManagedBy = "Terraform"
  })
}

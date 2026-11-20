resource "aws_route53_zone" "this" {
  name          = var.domain_name
  comment       = "Delegated CanopyOps public application zone"
  force_destroy = false
  tags          = merge(var.tags, { Name = var.domain_name, Purpose = "Application DNS" })

  lifecycle {
    prevent_destroy = true
  }
}

output "zone_id" {
  description = "Hosted zone identifier for certificate validation and ALB aliases"
  value       = aws_route53_zone.this.zone_id
}

output "name_servers" {
  description = "Add each nameserver as an NS record at the parent DNS provider"
  value       = sort(aws_route53_zone.this.name_servers)
}

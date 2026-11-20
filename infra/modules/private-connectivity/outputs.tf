output "interface_endpoint_ids" {
  description = "Private interface endpoint IDs by service"
  value       = { for service, endpoint in aws_vpc_endpoint.interface : service => endpoint.id }
}
output "s3_endpoint_id" {
  description = "S3 image-layer gateway endpoint ID"
  value       = aws_vpc_endpoint.s3.id
}
output "s3_prefix_list_id" {
  description = "AWS-managed regional S3 prefix list used by task HTTPS egress"
  value       = aws_vpc_endpoint.s3.prefix_list_id
}

output "repositories" {
  description = "Private image repositories; no credentials are exported"
  value       = module.ecr.repositories
}

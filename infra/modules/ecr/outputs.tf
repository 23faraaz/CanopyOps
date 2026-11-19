output "repositories" {
  description = "Repository names, URLs and ARNs keyed by application component"
  value = {
    for service, repository in aws_ecr_repository.this : service => {
      name = repository.name
      url  = repository.repository_url
      arn  = repository.arn
    }
  }
}

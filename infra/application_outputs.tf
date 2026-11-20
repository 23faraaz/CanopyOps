output "application_metadata" {
  description = "Cloud service metadata only; no secret values or database contents"
  value = var.application == null ? null : {
    cluster_arn         = module.foundation["demo"].cluster_arn
    urls                = module.ingress["demo"].urls
    database            = module.database["demo"].database
    cache               = try(module.cache["demo"].cache, null)
    outbox_bucket       = module.foundation["demo"].outbox_bucket_name
    runtime_secret_arns = module.foundation["demo"].runtime_secret_arns
    services_active     = var.application.activate_services
  }
}

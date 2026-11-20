# Opt-in only. Existing network plans remain unchanged when application is null.
locals {
  application        = var.application == null ? {} : { demo = var.application }
  application_prefix = "${var.project_name}-${var.environment}"
  application_tags   = { Project = var.project_name, Environment = var.environment, ManagedBy = "Terraform" }
  service_instances = {
    for service in ["api", "worker", "analytics"] : service => var.application
    if var.application != null
  }
}
module "security" {
  for_each    = local.application
  source      = "./modules/network-security"
  name_prefix = local.application_prefix
  vpc_id      = module.network.vpc_id
  tags        = local.application_tags
}
module "queue" {
  for_each      = local.application
  source        = "./modules/queue"
  name_prefix   = local.application_prefix
  tags          = local.application_tags
  writes_paused = each.value.pause_ingestion
}
module "foundation" {
  for_each                 = local.application
  source                   = "./modules/ecs-foundation"
  name_prefix              = local.application_prefix
  aws_region               = var.aws_region
  repository_arns          = each.value.repository_arns
  permissions_boundary_arn = each.value.permissions_boundary_arn
  queue_arn                = "arn:aws:sqs:${var.aws_region}:${split(":", each.value.permissions_boundary_arn)[4]}:${local.application_prefix}-readings"
  redis_user_ids           = each.value.redis_user_ids
  generator_image_uri      = each.value.image_uris["worker"]
  tags                     = local.application_tags
}
module "database" {
  for_each                  = local.application
  source                    = "./modules/database"
  name_prefix               = local.application_prefix
  subnet_ids                = values(module.network.isolated_data_subnet_ids)
  security_group_ids        = [module.security[each.key].security_group_ids["postgres"]]
  log_kms_key_arn           = module.foundation[each.key].log_kms_key_arn
  engine_version            = each.value.postgres_version
  final_snapshot_identifier = each.value.final_snapshot_identifier
  tags                      = local.application_tags
  deletion_protection       = !each.value.teardown_prepared
}
module "cache" {
  for_each           = { for name, config in local.application : name => config if length(config.redis_user_ids) >= 2 }
  source             = "./modules/cache"
  name_prefix        = local.application_prefix
  subnet_ids         = values(module.network.isolated_data_subnet_ids)
  security_group_ids = [module.security[each.key].security_group_ids["redis"]]
  engine_version     = each.value.redis_version
  user_group_ids     = [module.foundation[each.key].redis_user_group_id]
  tags               = local.application_tags
}
module "ingress" {
  for_each               = local.application
  source                 = "./modules/ingress"
  name_prefix            = local.application_prefix
  subnet_ids             = values(module.network.public_subnet_ids)
  security_group_ids     = [module.security[each.key].security_group_ids["alb"]]
  vpc_id                 = module.network.vpc_id
  domain_name            = each.value.domain_name
  hosted_zone_id         = each.value.hosted_zone_id
  access_log_bucket_name = module.foundation[each.key].alb_log_bucket_name
  log_kms_key_arn        = module.foundation[each.key].log_kms_key_arn
  tags                   = local.application_tags
  depends_on             = [module.foundation]
  deletion_protection    = !each.value.teardown_prepared
}
module "connectivity" {
  for_each                   = local.application
  source                     = "./modules/private-connectivity"
  name_prefix                = local.application_prefix
  aws_region                 = var.aws_region
  vpc_id                     = module.network.vpc_id
  app_subnet_ids             = values(module.network.private_app_subnet_ids)
  app_route_table_ids        = [module.network.route_table_ids["app"]]
  endpoint_security_group_id = module.security[each.key].security_group_ids["endpoints"]
  task_security_group_ids    = { for name in ["api", "analytics", "worker", "generator"] : name => module.security[each.key].security_group_ids[name] }
  execution_role_arns        = values(module.foundation[each.key].execution_role_arns)
  repository_arns            = values(each.value.repository_arns)
  log_group_arns             = values(module.foundation[each.key].log_group_arns)
  secret_grants = {
    for name in ["api", "worker", "analytics"] : name => {
      role_arn    = module.foundation[each.key].execution_role_arns[name]
      secret_arns = concat([module.foundation[each.key].runtime_secret_arns[name]], name == "analytics" ? [module.foundation[each.key].runtime_secret_arns["redis"]] : [])
    }
  }
  producer_role_arn = module.foundation[each.key].task_role_arns["generator"]
  worker_role_arn   = module.foundation[each.key].task_role_arns["worker"]
  queue_arn         = module.queue[each.key].queue.arn
  outbox_access = {
    bucket_arn        = module.foundation[each.key].outbox_bucket_arn
    producer_role_arn = module.foundation[each.key].task_role_arns["generator"]
  }
  tags = local.application_tags
}
module "service" {
  for_each           = local.service_instances
  source             = "./modules/ecs-service"
  name_prefix        = local.application_prefix
  service_name       = each.key
  aws_region         = var.aws_region
  cluster_arn        = module.foundation["demo"].cluster_arn
  subnet_ids         = values(module.network.private_app_subnet_ids)
  security_group_ids = [module.security["demo"].security_group_ids[each.key]]
  log_kms_key_arn    = module.foundation["demo"].log_kms_key_arn
  image_uri          = each.value.image_uris[each.key]
  execution_role_arn = module.foundation["demo"].execution_role_arns[each.key]
  task_role_arn      = module.foundation["demo"].task_role_arns[each.key]
  desired_count      = each.value.activate_services ? 1 : 0
  environment_variables = merge({
    PGHOST                                     = module.database["demo"].database.address
    PGPORT                                     = tostring(module.database["demo"].database.port)
    PGDATABASE                                 = "canopy"
    PGSSLMODE                                  = "verify-full"
    AWS_REGION                                 = var.aws_region
    }, each.key == "worker" ? { SQS_QUEUE_NAME = "${local.application_prefix}-readings" } : {}, each.key == "analytics" && length(each.value.redis_user_ids) >= 2 ? {
    REDIS_HOST                                 = module.cache["demo"].cache.address
    REDIS_PORT                                 = tostring(module.cache["demo"].cache.port)
    REDIS_TLS                                  = "1"
  } : {})
  secrets = merge({
    PGUSER     = "${module.foundation["demo"].runtime_secret_arns[each.key]}:username::"
    PGPASSWORD = "${module.foundation["demo"].runtime_secret_arns[each.key]}:password::"
    }, each.key == "analytics" ? {
    REDIS_USERNAME = "${module.foundation["demo"].runtime_secret_arns["redis"]}:username::"
    REDIS_PASSWORD = "${module.foundation["demo"].runtime_secret_arns["redis"]}:password::"
  } : {})
  load_balancer = each.key == "worker" ? null : { target_group_arn = module.ingress["demo"].target_group_arns[each.key] }
  tags          = local.application_tags
  depends_on    = [module.connectivity, module.foundation, module.ingress]
}

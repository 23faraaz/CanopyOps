mock_provider "aws" {
  override_during = plan
}
variables {
  name_prefix        = "canopyops-test"
  tags               = { Project = "canopyops", Environment = "test" }
  subnet_ids         = ["subnet-11111111", "subnet-22222222"]
  security_group_ids = ["sg-11111111"]
  engine_version     = "7.1"
  user_group_ids     = ["canopyops-test-reader"]
}
run "encrypted_rbac_cache_with_bounded_baseline" {
  command = plan
  assert {
    condition     = aws_elasticache_replication_group.this.at_rest_encryption_enabled && aws_elasticache_replication_group.this.transit_encryption_enabled && aws_elasticache_replication_group.this.transit_encryption_mode == "required" && length(aws_elasticache_replication_group.this.user_group_ids) > 0
    error_message = "Require both encryption layers and an explicit RBAC group."
  }
  assert {
    condition     = aws_elasticache_replication_group.this.num_cache_clusters == 1 && !aws_elasticache_replication_group.this.automatic_failover_enabled && !aws_elasticache_replication_group.this.multi_az_enabled
    error_message = "A single cache node is the explicit disposable baseline, not a failover claim."
  }
}
run "explicit_two_node_exercise" {
  command = plan
  variables { node_count = 2 }
  assert {
    condition     = aws_elasticache_replication_group.this.automatic_failover_enabled && aws_elasticache_replication_group.this.multi_az_enabled
    error_message = "Two nodes must enable the matching availability controls."
  }
}
run "reject_unauthenticated_cache" {
  command = plan
  variables { user_group_ids = [] }
  expect_failures = [var.user_group_ids]
}
run "reject_unbounded_cache_count" {
  command = plan
  variables { node_count = 3 }
  expect_failures = [var.node_count]
}

run "reject_extra_unreviewed_user_group" {
  command = plan
  variables { user_group_ids = ["fixture-reader", "fixture-extra"] }
  expect_failures = [var.user_group_ids]
}

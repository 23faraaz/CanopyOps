mock_provider "aws" {
  override_during = plan
}
override_resource {
  override_during = plan
  target          = aws_sqs_queue.readings
  values          = { arn = "arn:aws:sqs:eu-west-2:123456789012:canopyops-test-readings", url = "https://sqs.eu-west-2.amazonaws.com/123456789012/canopyops-test-readings" }
}
override_resource {
  override_during = plan
  target          = aws_sqs_queue.dead_letter
  values          = { arn = "arn:aws:sqs:eu-west-2:123456789012:canopyops-test-dlq", url = "https://sqs.eu-west-2.amazonaws.com/123456789012/canopyops-test-dlq" }
}
variables {
  name_prefix = "canopyops-test"
  tags        = { Project = "canopyops", Environment = "test" }
}
run "encrypted_standard_queue_and_scoped_redrive" {
  command = plan
  assert {
    condition     = aws_sqs_queue.readings.sqs_managed_sse_enabled && aws_sqs_queue.dead_letter.sqs_managed_sse_enabled && !aws_sqs_queue.readings.fifo_queue && jsondecode(aws_sqs_queue.readings.redrive_policy).deadLetterTargetArn == aws_sqs_queue.dead_letter.arn && jsondecode(aws_sqs_queue_redrive_allow_policy.this.redrive_allow_policy).sourceQueueArns == [aws_sqs_queue.readings.arn]
    error_message = "Use encrypted standard queues and allow only the source queue to redrive into this DLQ."
  }
  assert {
    condition     = alltrue([for name, queue in aws_sqs_queue_policy.https_only : jsondecode(queue.policy).Statement[0].Effect == "Deny" && jsondecode(queue.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"])
    error_message = "Both queues must reject insecure transport."
  }
}
run "reject_shorter_dlq_retention" {
  command = plan
  variables { dead_letter_retention_seconds = 3600 }
  expect_failures = [var.dead_letter_retention_seconds]
}
run "reject_unbounded_retries" {
  command = plan
  variables { max_receive_count = 11 }
  expect_failures = [var.max_receive_count]
}
run "reject_too_short_visibility" {
  command = plan
  variables { visibility_timeout_seconds = 1 }
  expect_failures = [var.visibility_timeout_seconds]
}

run "fenced_ingestion_keeps_tls_and_dlq_delivery" {
  command = plan
  variables { writes_paused = true }
  override_resource {
    override_during = plan
    target          = aws_sqs_queue.readings
    values          = { arn = "arn:aws:sqs:eu-west-2:123456789012:test-readings", url = "https://sqs.eu-west-2.amazonaws.com/123456789012/test-readings" }
  }
  override_resource {
    override_during = plan
    target          = aws_sqs_queue.dead_letter
    values          = { arn = "arn:aws:sqs:eu-west-2:123456789012:test-dlq", url = "https://sqs.eu-west-2.amazonaws.com/123456789012/test-dlq" }
  }
  assert {
    condition     = length(jsondecode(aws_sqs_queue_policy.https_only["readings"].policy).Statement) == 2 && length(jsondecode(aws_sqs_queue_policy.https_only["dead_letter"].policy).Statement) == 1 && jsondecode(aws_sqs_queue_policy.https_only["readings"].policy).Statement[1].Effect == "Deny"
    error_message = "The fence must deny new source sends without removing HTTPS protection or blocking automatic DLQ delivery."
  }
}

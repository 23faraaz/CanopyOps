locals {
  tags = merge(var.tags, { ManagedBy = "Terraform", Service = "ingestion" })
}
resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.name_prefix}-readings-dlq"
  message_retention_seconds = var.dead_letter_retention_seconds
  sqs_managed_sse_enabled   = true
  tags                      = merge(local.tags, { Name = "${var.name_prefix}-readings-dlq" })
}
resource "aws_sqs_queue" "readings" {
  name                       = "${var.name_prefix}-readings"
  fifo_queue                 = false
  message_retention_seconds  = var.retention_seconds
  visibility_timeout_seconds = var.visibility_timeout_seconds
  receive_wait_time_seconds  = 10
  max_message_size           = 262144
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = var.max_receive_count
  })
  tags = merge(local.tags, { Name = "${var.name_prefix}-readings" })
}
resource "aws_sqs_queue_redrive_allow_policy" "this" {
  queue_url = aws_sqs_queue.dead_letter.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.readings.arn]
  })
}
resource "aws_sqs_queue_policy" "https_only" {
  for_each  = { readings = aws_sqs_queue.readings, dead_letter = aws_sqs_queue.dead_letter }
  queue_url = each.value.url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*"
      Action    = "sqs:*", Resource = each.value.arn
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }], var.writes_paused && each.key == "readings" ? [{
      Sid       = "FenceNewReadings"
      Effect    = "Deny"
      Principal = "*"
      Action    = ["sqs:SendMessage"]
      Resource  = each.value.arn
    }] : [])
  })
}

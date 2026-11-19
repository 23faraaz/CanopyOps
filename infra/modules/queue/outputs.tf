output "queue" {
  description = "Standard queue ARN/URL and visibility setting for producer, worker and scoped IAM"
  value       = { arn = aws_sqs_queue.readings.arn, url = aws_sqs_queue.readings.url, visibility_timeout_seconds = var.visibility_timeout_seconds }
}
output "dead_letter_queue" {
  description = "DLQ identifiers; this module does not automatically redrive messages"
  value       = { arn = aws_sqs_queue.dead_letter.arn, url = aws_sqs_queue.dead_letter.url }
}

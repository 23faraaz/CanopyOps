resource "aws_iam_role" "terraform_demo" {
  name = "canopyops-demo-terraform-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        AWS = var.terraform_operator_arn
      }
    }]
  })
}

resource "aws_iam_role_policy" "demo_state" {
  name = "demo-state-access"
  role = aws_iam_role.terraform_demo.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListDemoStatePrefix"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.terraform_state.arn

        Condition = {
          StringLike = {
            "s3:prefix" = "canopyops/demo/*"
          }
        }
      },
      {
        Sid    = "ReadWriteDemoState"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.terraform_state.arn}/canopyops/demo/terraform.tfstate"
        ]
      },
      {
        Sid    = "ManageDemoStateLock"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${aws_s3_bucket.terraform_state.arn}/canopyops/demo/terraform.tfstate.tflock"
        ]
      }
    ]
  })
}
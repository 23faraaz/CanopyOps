terraform {
  backend "s3" {
    bucket       = "canopyops-demo-tfstate-23faraaz"
    key          = "canopyops/bootstrap/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }
}

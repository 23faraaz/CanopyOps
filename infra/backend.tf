terraform {
  backend "s3" {
    bucket       = "canopyops-demo-tfstate-23faraaz"
    key          = "canopyops/demo/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }
}
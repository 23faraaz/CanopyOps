terraform {
  backend "s3" {
    bucket               = "canopyops-demo-tfstate-23faraaz"
    key                  = "canopyops/registry/terraform.tfstate"
    region               = "eu-west-2"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "canopyops/registry/workspaces"
  }
}

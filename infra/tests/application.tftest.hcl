mock_provider "aws" { override_during = plan }
variables {
  project_name               = "canopyops"
  environment                = "demo"
  aws_region                 = "eu-west-2"
  availability_zones         = ["eu-west-2a", "eu-west-2b"]
  vpc_cidr                   = "10.42.0.0/16"
  public_subnet_cidrs        = ["10.42.0.0/24", "10.42.1.0/24"]
  private_app_subnet_cidrs   = ["10.42.10.0/24", "10.42.11.0/24"]
  isolated_data_subnet_cidrs = ["10.42.20.0/24", "10.42.21.0/24"]
}
run "network_only_default" {
  command = plan
  assert {
    condition     = length(module.service) == 0 && length(module.connectivity) == 0 && length(module.database) == 0 && output.application_metadata == null
    error_message = "The default must preserve the network-only deployment scope."
  }
}

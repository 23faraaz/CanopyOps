module "network" {
  source = "./modules/network"

  vpc_cidr                   = var.vpc_cidr
  public_subnet_cidrs        = var.public_subnet_cidrs
  private_app_subnet_cidrs   = var.private_app_subnet_cidrs
  isolated_data_subnet_cidrs = var.isolated_data_subnet_cidrs
  availability_zones         = var.availability_zones
  project_name               = var.project_name
}
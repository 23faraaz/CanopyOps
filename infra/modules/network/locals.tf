locals {
  subnet_tiers = {
    public = var.public_subnet_cidrs
    app    = var.private_app_subnet_cidrs
    data   = var.isolated_data_subnet_cidrs
  }

  subnets = merge([
    for tier, cidrs in local.subnet_tiers : {
      for index, cidr in cidrs :
      "${tier}-${index == 0 ? "a" : "b"}" => {
        cidr_block        = cidr
        availability_zone = var.availability_zones[index]
        tier              = tier
      }
    }
  ]...)
}
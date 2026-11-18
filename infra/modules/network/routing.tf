resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

resource "aws_route_table" "tier" {
  for_each = local.subnet_tiers

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project_name}-${each.key}-routes"
    Tier = each.key
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.tier["public"].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "subnet" {
  for_each = local.subnets

  subnet_id      = aws_subnet.this[each.key].id
  route_table_id = aws_route_table.tier[each.value.tier].id
}

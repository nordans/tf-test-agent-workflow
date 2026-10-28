terraform {
  required_providers {
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}

data "external" "must_be_mocked" {
  program = ["/program-must-never-run"]
}

output "revision" {
  value = data.external.must_be_mocked.result.revision
}

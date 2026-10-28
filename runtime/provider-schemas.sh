#!/bin/sh
set -eu
cp /opt/schema.tf /work/main.tf
cat /opt/provider/azurerm.lock.hcl /opt/provider/external.lock.hcl >/work/.terraform.lock.hcl
terraform init -backend=false -input=false -lockfile=readonly -no-color >/work/init.log 2>&1
terraform providers schema -json

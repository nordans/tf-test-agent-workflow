#!/bin/sh
# Fixed commands only. /input is read-only; /work is an ephemeral tmpfs.
set -eu
cp /input/*.tf /work/
cp /opt/provider/azurerm.lock.hcl /work/.terraform.lock.hcl
mkdir /work/tests
cp /input/generated.tftest.hcl /work/tests/generated.tftest.hcl

printf '{"runner_stage":"format"}\n'
if ! terraform fmt -no-color tests/generated.tftest.hcl >/work/fmt.log 2>&1; then
    printf '{"runner_failure":"format"}\n'
    exit 1
fi
printf '{"formatted_hcl_base64":"'
base64 /work/tests/generated.tftest.hcl | tr -d '\n'
printf '"}\n'

printf '{"runner_stage":"init"}\n'
if ! terraform init -backend=false -input=false -lockfile=readonly -no-color >/work/init.log 2>&1; then
    printf '{"runner_failure":"init"}\n'
    exit 1
fi
printf '{"runner_stage":"validate"}\n'
if ! terraform validate -json; then
    printf '{"runner_failure":"validate"}\n'
    exit 1
fi
printf '{"runner_stage":"test"}\n'
terraform test -json

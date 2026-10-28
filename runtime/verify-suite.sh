#!/bin/sh
set -eu
cp /input/*.tf /work/
cp /input/.terraform.lock.hcl /work/.terraform.lock.hcl
mkdir /work/tests
cp /input/tests/*.tftest.hcl /work/tests/
chmod u+w /work/*.tf /work/tests/*.tftest.hcl
printf '{"runner_stage":"format"}\n'
terraform fmt -no-color -recursive /work >/work/fmt.log 2>&1
for test in /work/tests/*.tftest.hcl; do
    printf '{"formatted_test":"tests/%s","content_base64":"' "$(basename "$test")"
    base64 "$test" | tr -d '\n'
    printf '"}\n'
done
printf '{"runner_stage":"init"}\n'
if ! terraform init -json -backend=false -input=false -lockfile=readonly -no-color; then
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

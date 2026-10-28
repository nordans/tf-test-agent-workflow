#!/bin/sh
# Validation reads schemas only; it never plans resources or evaluates external programs.
set -eu
mkdir /work/repository
cp -R /input/. /work/repository/
cd "/work/repository/$1"
if ! terraform init -backend=false -input=false -lockfile=readonly -no-color >/work/init.log 2>&1; then
    printf '{"valid":false,"diagnostics":[{"summary":"Offline init failed; provider or local module requirements do not match the approved runtime."}]}\n'
    exit 1
fi
terraform validate -json

# Terraform 1.17.1, linux/amd64. Resolve upgrades explicitly and review the digest.
FROM hashicorp/terraform:1.17.1@sha256:8cffe811b97bb7db1b8aa14eff94b97f1095eb3394bdd03b3e916249c28091f5

WORKDIR /opt/provider
COPY runtime/providers.tf ./main.tf
COPY runtime/azurerm.lock.hcl ./.terraform.lock.hcl
# The committed lock hashes authenticate the package; init adds its Linux h1 hash.
# An unpacked mirror lets runtime init symlink to the trusted executable here.
RUN terraform init -backend=false -input=false \
    && mkdir /opt/provider-mirror \
    && mv .terraform/providers/registry.terraform.io /opt/provider-mirror/

COPY runtime/terraform.rc /opt/terraform.rc
COPY runtime/verify.sh /opt/verify.sh
ENV TF_CLI_CONFIG_FILE=/opt/terraform.rc TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["/bin/sh", "/opt/verify.sh"]

# Terraform 1.17.1, linux/amd64. Resolve upgrades explicitly and review the digest.
FROM hashicorp/terraform:1.17.1@sha256:8cffe811b97bb7db1b8aa14eff94b97f1095eb3394bdd03b3e916249c28091f5 AS external-build
RUN wget -q -O /tmp/go.tar.gz https://go.dev/dl/go1.27.3.linux-amd64.tar.gz \
    && echo '65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40  /tmp/go.tar.gz' | sha256sum -c - \
    && tar -C /opt -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz
ENV PATH=/opt/go/bin:$PATH GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=2 GOFLAGS=-p=2 GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
WORKDIR /source
RUN wget -q -O /tmp/source.tar.gz https://codeload.github.com/hashicorp/terraform-provider-external/tar.gz/d7872aa77c01e493469b7eb95ba98074aea4e8d5 \
    && echo '962da5e903d3421d066a8aec7239f61825e1aeda59e744205fa9642bb64738a1  /tmp/source.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 \
    && rm /tmp/source.tar.gz
COPY runtime/external-build/go.mod runtime/external-build/go.sum ./
RUN go mod download \
    && go mod verify \
    && go test -mod=readonly ./internal/provider \
    && mkdir /output \
    && go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
       -o /output/terraform-provider-external_v2.4.2+patched.1 . \
    && cp LICENSE /output/LICENSE

FROM hashicorp/terraform:1.17.1@sha256:8cffe811b97bb7db1b8aa14eff94b97f1095eb3394bdd03b3e916249c28091f5

WORKDIR /opt/provider
COPY runtime/providers.tf ./main.tf
COPY runtime/azurerm.lock.hcl ./azurerm.lock.hcl
# The committed lock hashes authenticate the package; init adds its Linux h1 hash.
# An unpacked mirror lets runtime init symlink to the trusted executable here.
RUN cp azurerm.lock.hcl .terraform.lock.hcl \
    && terraform init -backend=false -input=false \
    && mkdir /opt/provider-mirror \
    && mv .terraform/providers/registry.terraform.io /opt/provider-mirror/

COPY --from=external-build /output/ /opt/provider-mirror/registry.terraform.io/hashicorp/external/2.4.2+patched.1/linux_amd64/
COPY runtime/external.lock.hcl /opt/provider/external.lock.hcl
COPY runtime/external-build/provider.tf /opt/external-check/main.tf

COPY runtime/terraform.rc /opt/terraform.rc
COPY runtime/verify.sh /opt/verify.sh
COPY runtime/check-module.sh /opt/check-module.sh
COPY runtime/external-smoke.tf /opt/external-smoke/main.tf
COPY runtime/external-smoke.tftest.hcl /opt/external-smoke/tests/main.tftest.hcl
ENV TF_CLI_CONFIG_FILE=/opt/terraform.rc TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1
RUN cp /opt/provider/external.lock.hcl /opt/external-check/.terraform.lock.hcl \
    && terraform -chdir=/opt/external-check init -backend=false -input=false -lockfile=readonly
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["/bin/sh", "/opt/verify.sh"]

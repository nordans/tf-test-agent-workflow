# Patched external provider for the offline test runtime

The official external 2.4.2 Linux package was built with Go 1.26.8 and x/net
0.58.0. The image scan found five HIGH and one CRITICAL advisories. This runtime
rebuilds the same provider source with Go 1.27.3 and x/net 0.62.0, including the
transitive dependency updates frozen in go.mod and go.sum. It does not change
the provider's Go implementation or the infrastructure's external program.

Source: [HashiCorp commit d7872aa](https://github.com/hashicorp/terraform-provider-external/tree/d7872aa77c01e493469b7eb95ba98074aea4e8d5), the upstream v2.4.2 tag.
Source archive SHA-256: `962da5e903d3421d066a8aec7239f61825e1aeda59e744205fa9642bb64738a1`.
Go archive SHA-256: `65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40`.

The Docker build verifies both archives and Go module checksums, runs upstream
provider tests, and builds with readonly module resolution, disabled VCS stamping,
trimmed paths and CGO disabled. Terraform sees version 2.4.2+patched.1 in the local
mirror. This is a locally maintained build, not an official HashiCorp release.
The provider's upstream MPL-2.0 license is copied next to the binary. The source
and dependency manifests are public so this test-only build can be reproduced.

The external lockfile authenticates the rebuilt package, not the upstream release
archive. Never reuse the upstream package hashes for this build. Updating Go,
source, dependencies, build flags or packaged files requires regenerating and
reviewing the local package hash. Build the external-build Docker stage, construct
an unpacked filesystem mirror from its /output directory, and use Terraform
providers lock -fs-mirror against that mirror to calculate it. Keep the final
readonly init check and HIGH/CRITICAL image gate enabled.

On upgrades, review upstream changes, refresh the frozen dependency manifests,
run provider tests, verify offline schema compatibility and mocked external data,
and scan the final image. Move back to an official package when an upstream release
passes these checks. This local build adds maintenance responsibility and does not
replace review of the provider or its advisories.

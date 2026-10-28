#!/usr/bin/env bash
# Pinned Linux amd64 release archives; never execute a downloaded installer.
set -euo pipefail

tool="${1:?Select actionlint, betterleaks or trivy}"
case "$tool" in
  actionlint)
    url='https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz'
    checksum='8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8'
    ;;
  betterleaks)
    url='https://github.com/betterleaks/betterleaks/releases/download/v1.9.0/betterleaks_1.9.0_linux_x64.tar.gz'
    checksum='f8b185a39ffcece2a1ca82bf3a4e7435cd81963ffd16b7a9128daf75f35f6de7'
    ;;
  trivy)
    url='https://github.com/aquasecurity/trivy/releases/download/v0.76.1/trivy_0.76.1_Linux-64bit.tar.gz'
    checksum='c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0'
    ;;
  *)
    printf 'Unsupported security tool: %s\n' "$tool" >&2
    exit 1
    ;;
esac

install_dir="${RUNNER_TEMP:?}/security-tools/$tool"
mkdir -p "$install_dir"
archive=$(mktemp "${RUNNER_TEMP}/security-tool.XXXXXX")
trap 'rm -f -- "$archive"' EXIT
curl --fail --location --proto '=https' --tlsv1.2 \
  --connect-timeout 10 --max-time 120 --retry 3 \
  --output "$archive" "$url"
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
tar --extract --gzip --file "$archive" --directory "$install_dir" "$tool"
chmod 755 "$install_dir/$tool"
printf '%s\n' "$install_dir" >> "${GITHUB_PATH:?}"

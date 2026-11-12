#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
verify_image() {
  local tag="$1" expected="$2" actual
  actual="$(docker image inspect --format '{{.Id}}' "$tag")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Refusing unverified local image: $tag" >&2
    exit 1
  fi
}
verify_image poststeward-release-runner:python310-312-uv-20261109 \
  sha256:5fa59aef5e9f5507f1abbf2fcc9fd74b69a26d9d911e14058b4664b841c4500e
verify_image poststeward-verification-runner:python3.12.16-node24 \
  sha256:9ff5f94e3dc808d524f680b4b8ca25a98ee2cd6098fe997facbca1a184ee08a8
docker build --pull=false --network=none \
  -f ops/verification-runner-python-matrix/Dockerfile \
  -t poststeward-verification-runner:python310-312-offline \
  ops/verification-runner-python-matrix

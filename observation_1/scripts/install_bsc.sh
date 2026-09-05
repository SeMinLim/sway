#!/usr/bin/env bash
set -euo pipefail
prefix="${1:-$HOME/.local/opt/sway-bsc}"
archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT
curl --fail --location --retry 3 \
  https://github.com/B-Lang-org/bsc/releases/download/2026.01/bsc-2026.01-debian-12.13.tar.gz \
  --output "$archive"
printf '%s  %s\n' 9da36623e301ae14ba5e670cb98d11c99277faa915025ccb7615de3ec14002c3 "$archive" | sha256sum --check
mkdir -p "$prefix"
tar -xzf "$archive" --strip-components=1 -C "$prefix"
printf 'export PATH="%s/bin:$PATH"\n' "$prefix"
"$prefix/bin/bsc" -v

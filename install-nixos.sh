#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$root"
nix-build runtime.nix -o .nix-runtime
export LD_LIBRARY_PATH="$root/.nix-runtime/lib:${LD_LIBRARY_PATH:-}"
.nix-runtime/bin/uv venv --allow-existing --python "$root/.nix-runtime/bin/python3.12" .venv
.nix-runtime/bin/uv sync --frozen --extra test
chmod +x bin/findmatch
mkdir -p "$HOME/.local/bin"
if [[ -e "$HOME/.local/bin/findmatch" || -L "$HOME/.local/bin/findmatch" ]]; then
  if [[ "$(readlink -f "$HOME/.local/bin/findmatch")" != "$root/bin/findmatch" ]]; then
    printf '%s\n' 'Refusing to replace existing ~/.local/bin/findmatch' >&2
    exit 1
  fi
else
  ln -s "$root/bin/findmatch" "$HOME/.local/bin/findmatch"
fi
printf '%s\n' "Installed $HOME/.local/bin/findmatch"

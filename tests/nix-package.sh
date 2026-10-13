#!/usr/bin/env bash
# Acceptance test: package runs without the checkout's virtual environment.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package="$(nix build "path:$root#findmatch" --no-link --print-out-paths)"
work="$(mktemp -d "${TMPDIR:?}/findmatch-package.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
cd "$work"
env -u PYTHONPATH -u LD_LIBRARY_PATH PATH=/run/current-system/sw/bin "$package/bin/findmatch" --help > help.txt
for text in 'EmbeddingGemma 2' 'Examples:' 'metadata only' '--gui' 'GPUI'; do
  test "$(python3 -c 'import sys; print(sys.argv[1] in open("help.txt").read())' "$text")" = True
done
mkdir files
printf 'antenna calibration\n' > files/note.txt
set +e
printf 'no\n' | env -u PYTHONPATH -u LD_LIBRARY_PATH PATH=/run/current-system/sw/bin "$package/bin/findmatch" files antenna --cache-dir "$work/cache" > results.txt 2> error.txt
status=$?
set -e
test "$status" = 2
test ! -s results.txt
test ! -e cache
printf 'Package help and no-consent behavior verified: %s\n' "$package"

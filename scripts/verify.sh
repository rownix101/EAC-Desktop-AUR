#!/usr/bin/env bash
# Local checks for this AUR package, mirroring what CI runs.
#
#   ./scripts/verify.sh          # fast checks (no build)
#   ./scripts/verify.sh --build  # also run a real makepkg build
#
# Requires: makepkg (pacman), python3, bsdtar (libarchive).

set -euo pipefail

cd "$(dirname "$0")/.."
DO_BUILD=false
[[ ${1:-} == '--build' ]] && DO_BUILD=true

pass() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; exit 1; }
info() { printf '\033[36m----\033[0m %s\n' "$*"; }

# makepkg refuses to run as root. CI runs this script inside an Arch container as
# root, so re-exec makepkg as the unprivileged build user when needed.
BUILDER_USER=${BUILDER_USER:-builder}
makepkg_run() {
  if [[ $(id -u) -eq 0 ]]; then
    id -u "$BUILDER_USER" >/dev/null 2>&1 \
      || fail "running as root but user '$BUILDER_USER' does not exist"
    sudo -u "$BUILDER_USER" --preserve-env=PWD makepkg "$@"
  else
    makepkg "$@"
  fi
}

info 'required tools'
for tool in makepkg python3 bsdtar; do
  command -v "$tool" >/dev/null || fail "$tool is missing"
done
pass 'tooling present'

info 'bash syntax of PKGBUILD'
bash -n PKGBUILD || fail 'PKGBUILD has a syntax error'
pass 'PKGBUILD parses'

info 'PKGBUILD metadata'
# shellcheck disable=SC1091
source PKGBUILD
[[ -n ${pkgname} && -n ${pkgver} && -n ${pkgrel} ]] || fail 'missing pkgname/pkgver/pkgrel'
[[ ${arch[*]} == 'x86_64' ]] || fail 'arch must be x86_64'
[[ ${source[0]} == *"${_tag}"* ]] || fail '_tag does not appear in source URL'
[[ ${source[0]} == *"${_debname}"* ]] || fail '_debname does not appear in source URL'
[[ ${source[0]} == *.deb* ]] || fail 'source is not a .deb'
[[ ${#sha256sums[@]} -eq ${#source[@]} ]] || fail 'sha256sums count != source count'
[[ ${sha256sums[0]} =~ ^[0-9a-f]{64}$ ]] || fail 'sha256sums[0] is not a sha256'
[[ ${pkgver} != *-* ]] || fail 'pkgver must not contain a hyphen'
pass "metadata ok (${pkgname} ${pkgver}-${pkgrel})"

info '.SRCINFO is in sync'
expected=$(makepkg_run --printsrcinfo)
if [[ $expected == "$(cat .SRCINFO)" ]]; then
  pass '.SRCINFO matches PKGBUILD'
else
  diff -u <(printf '%s\n' "$expected") .SRCINFO || true
  fail '.SRCINFO is stale; regenerate with: makepkg --printsrcinfo > .SRCINFO'
fi

info 'dependency names exist'
if command -v pacman >/dev/null; then
  missing=()
  for dep in "${depends[@]}"; do
    pacman -Si "$dep" >/dev/null 2>&1 || missing+=("$dep")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    fail "unknown dependency: ${missing[*]}"
  fi
  pass 'all runtime dependencies resolve'
fi

info 'upstream release discovery'
python3 scripts/update-pkgbuild.py --check >/dev/null || fail 'upstream check failed'
pass 'upstream reachable and a .deb was found'

if [[ ${DO_BUILD} == true ]]; then
  info 'build'
  makepkg_run -f -s --noconfirm
  pkg=$(find . -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -print -quit)
  [[ -n $pkg ]] || fail 'no package produced'

  bsdtar -tvf "$pkg" > /tmp/verify.pkg.list
  for path in 'usr/bin/dsh-eac-shell' \
              'Deepseek Harness EAC/sidecar/server.js' \
              'Deepseek Harness EAC/dsh-desktop/vendor/node/node' \
              'usr/share/applications/Deepseek Harness EAC.desktop'; do
    grep -q "$path" /tmp/verify.pkg.list || fail "package is missing $path"
  done
  pass "built $(basename "$pkg")"
fi

info 'done'

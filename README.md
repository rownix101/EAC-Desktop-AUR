# deepseek-harness-eac-bin — AUR package

Arch Linux packaging for [EAC-Desktop (DeepSeek Harness EAC)](https://github.com/Ebony-Vinyl/EAC-Desktop),
an MIT-licensed desktop workbench for DSH plugins.

Upstream publishes Debian packages but no Arch package, so this repo repackages
the upstream amd64 `.deb` and keeps it current automatically.

## Install

```bash
yay -S deepseek-harness-eac-bin
# or
paru -S deepseek-harness-eac-bin
```

Then launch **Deepseek Harness EAC** from your application menu, or run:

```bash
dsh-eac-shell
```

## Files

| Path | Purpose |
| --- | --- |
| `PKGBUILD` | The package definition (repack only, nothing is compiled) |
| `.SRCINFO` | Generated metadata the AUR requires |
| `scripts/update-pkgbuild.py` | Finds the newest upstream `.deb` and bumps the PKGBUILD |
| `scripts/verify.sh` | Local checks, the same ones CI runs |
| `.github/workflows/aur-update.yml` | Scheduled upstream check, build, and AUR push |

## How the update automation works

`.github/workflows/aur-update.yml` runs every 6 hours (and on demand):

1. **Find the newest `.deb`.** It scans *all* upstream releases rather than
   looking at "latest release".
2. **Bump** `pkgver`, `pkgrel`, `_tag`, `_debname`, and `sha256sums` in the
   `PKGBUILD`, then regenerate `.SRCINFO`.
3. **Verify.** `scripts/verify.sh`, a real `makepkg` build in an
   `archlinux:base-devel` container, and a check that the payload is intact.
4. **Commit** the bump, then **push to the AUR**.

### Setup

This repo lives at <https://github.com/rownix101/EAC-Desktop-AUR> and the
following are already configured in *Settings → Secrets and variables → Actions*:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `AUR_SSH_PRIVATE_KEY` | Private SSH key registered in your AUR account |
| Variable | `AUR_USERNAME` | Your AUR username |
| Variable | `AUR_EMAIL` | Optional commit email |

The registered public key is:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHz4go6vCrK6MDA3wTl/PZlT4Tuq2SmNLFM8zHNBAZkp aur-eac-desktop
```

Paste it at <https://aur.archlinux.org/account> → *SSH Public Key*. The matching
private half is only in the `AUR_SSH_PRIVATE_KEY` secret (and in the local,
git-ignored `.aur-key/` if you generated it there).

Without the secret the workflow still bumps the `PKGBUILD` and builds it, and
simply skips the upload.

### First-time AUR submission

The AUR only creates `deepseek-harness-eac-bin.git` after the package has been
submitted once through the web UI; before that, `git clone` over SSH fails and
the publish step prints a notice instead of pushing. So, once:

1. Register the public key above on your AUR account.
2. Go to <https://aur.archlinux.org/submit/> and submit a *package base* named
   `deepseek-harness-eac-bin` (upload the `PKGBUILD` + `.SRCINFO` from this
   repo, or the tarball).
3. Run *Actions → Update AUR package → Run workflow* with **force** ticked.

From then on the scheduled run pushes version bumps on its own.

You can also run it manually from *Actions → Update AUR package → Run workflow*.
Tick **force** to rebuild and republish the current version with a bumped
`pkgrel` (useful after a packaging fix).

## Upstream quirks this package works around

These are deliberate; please do not "clean them up".

**Linux builds are not in every release.** Newer tags such as `v6.0.x` ship
Windows artifacts only. The newest amd64 `.deb` is `v5.3.6`, while `v6.0.1` is
the newest release overall. Selecting by "latest release" would silently skip
Linux for months, so the updater picks the highest version that actually
contains an amd64 `.deb`.

**The tag is not always `v${pkgver}`.** Upstream has used `v4.4.0-linux` and
`v5.3.1-linux`, and the asset name varies too (`Deepseek.Harness.EAC_5.3.6_amd64.deb`
vs `Deepseek-Harness-EAC-4.4.0-amd64.deb`). The exact tag and filename are
stored in `_tag` and `_debname` instead of being derived.

**The payload keeps its Debian paths.** Files stay under
`/usr/lib/Deepseek Harness EAC/` and the binary stays at `/usr/bin/dsh-eac-shell`.
The Tauri shell resolves its resources relative to the real executable and also
probes `/usr/lib/<productName>`; relocating the payload produces

```
[shell] ignoring invalid resource directory: /usr/lib/Deepseek Harness EAC
[sidecar] spawning node=node script=./tauri-shell/sidecar/server.js
[shell] boot.start failed: sidecar exited
```

and the app never starts. `dsh-desktop/` also must not be pruned:
`bundle-integrity.js` compares per-package file counts against
`bundle-manifest.json` at boot and reports a damaged bundle if files are
missing. The package therefore only fixes the desktop entry's empty
`Categories=` and adds `StartupNotify=true`; both are safe, since neither file is
validated at runtime.

**No maintainer scripts.** The upstream `.deb` has none, and none are needed:
pacman's own hooks refresh the desktop database and icon cache.

## Building locally

```bash
./scripts/verify.sh          # fast checks
./scripts/verify.sh --build  # also build the package
makepkg -f -s                # build only
makepkg -si                  # build and install
```

The package is `-bin`: it downloads the upstream `.deb` (about 165 MB) and
repacks it, so builds are fast but require network access.

## Notes

- `x86_64` only, matching the single upstream Linux artifact.
- Bundles its own Node.js runtime, so no system Node.js is required.
- Upstream's in-app updater targets Windows installers; on Arch, updates arrive
  through this package.

## License

The packaging files here are provided under the same MIT terms as upstream.
EAC-Desktop itself is MIT — see the upstream repository.

#!/usr/bin/env python3
"""Keep this AUR package in sync with upstream EAC-Desktop Debian releases.

Commands
--------
  --check    Report what upstream currently offers. JSON goes to stdout.
  --update   Rewrite PKGBUILD (and .SRCINFO) when a newer amd64 .deb exists.

Only the Python standard library is used, so the script runs on a bare Arch or
Ubuntu image with no pip install.

Why the release list instead of "latest release": upstream publishes Linux
artifacts only for some tags (v6.0.x are Windows-only). Scanning every release
and picking the highest version that actually ships an amd64 .deb is the only
reliable signal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "Ebony-Vinyl/EAC-Desktop"
DEB_SUFFIX = "amd64.deb"
PKGBUILD = "PKGBUILD"
SRCINFO = ".SRCINFO"
UA = "eac-aur-updater/1.0 (+https://github.com/Ebony-Vinyl/EAC-Desktop)"

# "Deepseek.Harness.EAC_5.3.6_amd64.deb"   -> 5.3.6
# "Deepseek-Harness-EAC-4.4.0-amd64.deb"   -> 4.4.0
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+(?:[-+.][0-9A-Za-z]+)*)")
_API = "https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _download_sha256(url: str) -> str:
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _segments(version: str) -> list[str]:
    return re.findall(r"\d+|[A-Za-z]+", version)


def vercmp(a: str, b: str) -> int:
    """Approximate Arch vercmp: numeric segments compare numerically and sort
    above alphabetic ones; otherwise compare segment by segment."""
    sa, sb = _segments(a), _segments(b)
    for x, y in zip(sa, sb):
        if x.isdigit() and y.isdigit():
            if int(x) != int(y):
                return 1 if int(x) > int(y) else -1
        elif x.isdigit() != y.isdigit():
            return 1 if x.isdigit() else -1
        elif x != y:
            return 1 if x > y else -1
    if len(sa) != len(sb):
        return 1 if len(sa) > len(sb) else -1
    return 0


def to_pkgver(version: str) -> str:
    """Make a version string legal for Arch pkgver: no '-', ':' or spaces."""
    version = version.strip().lstrip("vV")
    version = version.replace("-", "_")
    return re.sub(r"[^0-9A-Za-z._+]", ".", version)


def fetch_releases(repo: str, max_pages: int = 5) -> list[dict]:
    releases: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            batch = _get_json(_API.format(repo=repo, page=page))
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"GitHub API error {exc.code} for {repo}: {exc.reason}")
        if not isinstance(batch, list) or not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
    return releases


def select_latest(repo: str, include_prerelease: bool = False,
                  rehash: bool = False) -> dict:
    """Newest stable release that actually ships an amd64 .deb."""
    best: dict | None = None
    for release in fetch_releases(repo):
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prerelease:
            continue
        for asset in release.get("assets") or []:
            name = asset.get("name") or ""
            if not name.endswith(DEB_SUFFIX):
                continue
            match = _VERSION_RE.search(name)
            if not match:
                log(f"warning: cannot parse a version out of {name!r}, skipping")
                continue
            version = match.group(1)
            if best is not None and vercmp(version, best["version"]) <= 0:
                continue
            digest = (asset.get("digest") or "")
            if rehash or not digest.startswith("sha256:"):
                log(f"hashing {name} ({asset.get('size', 0) / 1e6:.0f} MB) ...")
                sha256 = _download_sha256(asset["browser_download_url"])
            else:
                sha256 = digest.split(":", 1)[1]
            best = {
                "version": version,
                "pkgver": to_pkgver(version),
                "tag": release.get("tag_name") or "",
                "deb": name,
                "url": asset.get("browser_download_url") or "",
                "sha256": sha256,
                "size": asset.get("size") or 0,
                "published_at": release.get("published_at") or "",
            }
    if best is None:
        raise SystemExit(f"no amd64 .deb found in any release of {repo}")
    return best


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}=(.*)$", text, re.M)
    if not match:
        return None
    return match.group(1).strip().strip("'\"")


def _set_field(text: str, name: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.M)
    if not pattern.search(text):
        raise SystemExit(f"{PKGBUILD}: expected a '{name}=' line, none found")
    return pattern.sub(lambda _m: f"{name}={value}", text, count=1)


def _write_outputs(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _regen_srcinfo() -> bool:
    if shutil.which("makepkg") is None:
        log("warning: makepkg not found; .SRCINFO left untouched")
        return False
    proc = subprocess.run(["makepkg", "--printsrcinfo"], capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"warning: 'makepkg --printsrcinfo' failed: {proc.stderr.strip()}")
        return False
    with open(SRCINFO, "w", encoding="utf-8") as handle:
        handle.write(proc.stdout)
    log(f"regenerated {SRCINFO}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report upstream state as JSON; never writes files")
    parser.add_argument("--update", action="store_true",
                        help="rewrite PKGBUILD when a newer release exists")
    parser.add_argument("--force", action="store_true",
                        help="rewrite even if the version is unchanged (bumps pkgrel)")
    parser.add_argument("--rehash", action="store_true",
                        help="download the .deb and hash it instead of trusting the API digest")
    parser.add_argument("--include-prerelease", action="store_true",
                        help="consider pre-releases too (off by default)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="upstream GitHub repository")
    parser.add_argument("--pkgbuild", default=PKGBUILD, help="path to the PKGBUILD")
    args = parser.parse_args()

    if not (args.check or args.update):
        parser.error("pass --check and/or --update")

    latest = select_latest(args.repo, args.include_prerelease, args.rehash)

    try:
        with open(args.pkgbuild, encoding="utf-8") as handle:
            pkgbuild = handle.read()
    except FileNotFoundError:
        raise SystemExit(f"{args.pkgbuild} not found")

    current = _field(pkgbuild, "pkgver") or "0"
    current_rel = _field(pkgbuild, "pkgrel") or "1"
    update_available = vercmp(latest["pkgver"], current) > 0

    summary = {
        "repo": args.repo,
        "current": current,
        "current_pkgrel": current_rel,
        "latest": latest["pkgver"],
        "latest_raw_version": latest["version"],
        "tag": latest["tag"],
        "deb": latest["deb"],
        "url": latest["url"],
        "sha256": latest["sha256"],
        "published_at": latest["published_at"],
        "update_available": update_available,
    }

    changed = False
    if args.update and (update_available or args.force):
        new_pkgver = latest["pkgver"]
        if update_available:
            new_rel = "1"
        else:
            # Same version but forced: bump pkgrel so the rebuild is published.
            try:
                new_rel = str(int(current_rel) + 1)
            except ValueError:
                new_rel = "1"
        pkgbuild = _set_field(pkgbuild, "pkgver", new_pkgver)
        pkgbuild = _set_field(pkgbuild, "pkgrel", new_rel)
        pkgbuild = _set_field(pkgbuild, "_tag", f"'{latest['tag']}'")
        pkgbuild = _set_field(pkgbuild, "_debname", f"'{latest['deb']}'")
        pkgbuild = _set_field(pkgbuild, "sha256sums", f"('{latest['sha256']}')")
        with open(args.pkgbuild, "w", encoding="utf-8") as handle:
            handle.write(pkgbuild)
        changed = True
        summary["new_pkgrel"] = new_rel
        log(f"updated {args.pkgbuild}: {current}-{current_rel} -> {new_pkgver}-{new_rel}")
        _regen_srcinfo()

    summary["changed"] = changed
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    _write_outputs(
        update_available=str(update_available).lower(),
        changed=str(changed).lower(),
        version=latest["pkgver"],
        tag=latest["tag"],
        deb=latest["deb"],
        url=latest["url"],
        sha256=latest["sha256"],
        current=current,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

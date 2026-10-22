#!/usr/bin/env python3
"""Compose the Tauri updater manifest (latest.json) from staged release artifacts.

Run by the release CI job after all platform builds are staged in one directory:

    python3 make_update_manifest.py --version 0.1.2 --tag 0.1.2 \
        --repo PhongTran-code/Astra_AI --dist dist/ --out dist/latest.json

Looks for the updater artifacts by their STABLE names (the same names release.yml
uploads):

    Astra-macos-arm64.app.tar.gz(.sig)   -> platforms["darwin-aarch64"]
    Astra-macos-x64.app.tar.gz(.sig)     -> platforms["darwin-x86_64"]
    Astra-windows-setup.exe(.sig)        -> platforms["windows-x86_64"]
    Astra-linux-x86_64.AppImage(.sig)    -> platforms["linux-x86_64"]

URLs point at the TAG-pinned public download path supplied by --download-base-url,
never at `latest/` — a manifest must reference exactly the artifacts it shipped with,
or a half-published release would mix versions. Platforms whose artifact or .sig is
missing are SKIPPED with a warning (e.g. a mac-only hotfix release), so shipped apps
on other platforms simply see no update rather than a broken one.

The desktop app finds this file at the public endpoint configured in tauri.conf.json.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

# stable asset name -> Tauri platform key
ARTIFACTS = {
    "Astra-macos-arm64.app.tar.gz": "darwin-aarch64",
    "Astra-macos-x64.app.tar.gz": "darwin-x86_64",
    "Astra-windows-setup.exe": "windows-x86_64",
    "Astra-linux-x86_64.AppImage": "linux-x86_64",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True, help="bare version, e.g. 0.1.2")
    ap.add_argument(
        "--tag", required=True, help="git tag the assets live under, e.g. 0.1.2"
    )
    ap.add_argument("--repo", required=True, help="owner/name, e.g. PhongTran-code/Astra_AI")
    ap.add_argument(
        "--dist", required=True, type=pathlib.Path, help="staged artifacts dir"
    )
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument(
        "--notes", default="", help="release notes line shown in the update prompt"
    )
    ap.add_argument(
        "--download-base-url",
        default="",
        help="public URL prefix containing the versioned updater artifacts",
    )
    args = ap.parse_args()

    base_url = args.download_base_url.strip().rstrip("/")
    if not base_url:
        base_url = f"https://github.com/{args.repo}/releases/download/{args.tag}"

    platforms: dict[str, dict[str, str]] = {}
    for asset, platform in ARTIFACTS.items():
        artifact = args.dist / asset
        sig = args.dist / (asset + ".sig")
        if not artifact.exists():
            print(
                f"warning: {asset} not in {args.dist} — skipping {platform}",
                file=sys.stderr,
            )
            continue
        if not sig.exists():
            print(
                f"warning: {asset} has no .sig — skipping {platform} (unsigned updates never install)",
                file=sys.stderr,
            )
            continue
        platforms[platform] = {
            "signature": sig.read_text().strip(),
            "url": f"{base_url}/{asset}",
        }

    if not platforms:
        print(
            "error: no signed updater artifacts found — refusing to write an empty manifest",
            file=sys.stderr,
        )
        return 1

    manifest = {
        "version": args.version,
        "notes": args.notes,
        "pub_date": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "platforms": platforms,
    }
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {args.out} ({', '.join(sorted(platforms))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

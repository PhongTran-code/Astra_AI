"""Tauri updater manifest generation for public release hosting."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "packaging" / "make_update_manifest.py"


def _write_signed_bundle(dist: Path, name: str) -> None:
    (dist / name).write_bytes(b"bundle")
    (dist / f"{name}.sig").write_text(f"signature for {name}\n")


def test_manifest_uses_public_versioned_urls_for_every_supported_platform(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    bundles = {
        "Astra-macos-arm64.app.tar.gz": "darwin-aarch64",
        "Astra-macos-x64.app.tar.gz": "darwin-x86_64",
        "Astra-windows-setup.exe": "windows-x86_64",
        "Astra-linux-x86_64.AppImage": "linux-x86_64",
    }
    for name in bundles:
        _write_signed_bundle(dist, name)

    output = dist / "latest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--version",
            "0.2.4",
            "--tag",
            "v0.2.4",
            "--repo",
            "PhongTran-code/Astra_AI",
            "--download-base-url",
            "https://downloads.example.com/releases/v0.2.4",
            "--dist",
            str(dist),
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert set(manifest["platforms"]) == set(bundles.values())
    for asset, platform in bundles.items():
        assert (
            manifest["platforms"][platform]["url"]
            == f"https://downloads.example.com/releases/v0.2.4/{asset}"
        )
        assert manifest["platforms"][platform]["signature"]


def test_manifest_uses_new_repository_when_no_download_base_url_is_supplied(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = "Astra-linux-x86_64.AppImage"
    _write_signed_bundle(dist, asset)
    output = dist / "latest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--version",
            "0.2.4",
            "--tag",
            "v0.2.4",
            "--repo",
            "PhongTran-code/Astra_AI",
            "--dist",
            str(dist),
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert manifest["platforms"]["linux-x86_64"]["url"] == (
        f"https://github.com/PhongTran-code/Astra_AI/releases/download/v0.2.4/{asset}"
    )


def test_manifest_skips_unsigned_platform_without_writing_a_private_url(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_signed_bundle(dist, "Astra-linux-x86_64.AppImage")
    (dist / "Astra-windows-setup.exe").write_bytes(b"unsigned")
    output = dist / "latest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--version",
            "0.2.4",
            "--tag",
            "v0.2.4",
            "--repo",
            "PhongTran-code/Astra_AI",
            "--download-base-url",
            "https://downloads.example.com/releases/v0.2.4",
            "--dist",
            str(dist),
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert set(manifest["platforms"]) == {"linux-x86_64"}

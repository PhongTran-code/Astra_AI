#!/usr/bin/env bash
# Build the Linux desktop app: AppImage + .deb
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
APP="Astra"
VERSION="$(node -p "require('$GUI/package.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"

# Ensure pkg-config locates system libraries (alsa, webkit, ssl) even if PKG_CONFIG_PATH was modified
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}:/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/pkgconfig:/usr/share/pkgconfig"

echo "==> [1/3] PyInstaller: bundling astra-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/astra-server.spec"

echo "==> [2/3] staging sidecar resources"
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar"
cp -RL "$HERE/dist/astra-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/astra-server"

echo "==> [3/3] tauri build: creating AppImage and deb"
UPDATER_OVERLAY=""
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  UPDATER_OVERLAY="$(mktemp)"
  trap 'rm -f "$UPDATER_OVERLAY"' EXIT
  printf '%s\n' '{"bundle":{"createUpdaterArtifacts":true}}' > "$UPDATER_OVERLAY"
  ( cd "$GUI" && npm run tauri build -- --bundles appimage,deb --config "$UPDATER_OVERLAY" )
else
  echo "    WARNING: no updater signing key — building WITHOUT auto-update artifacts (not releasable)."
  ( cd "$GUI" && npm run tauri build -- --bundles appimage,deb )
fi

echo "==> Linux build complete!"
echo "Bundle output:"
ls -lh "$GUI/src-tauri/target/release/bundle/appimage/" || true
ls -lh "$GUI/src-tauri/target/release/bundle/deb/" || true

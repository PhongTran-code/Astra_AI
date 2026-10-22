# Changelog

All notable changes to Astra are documented here.

## [0.2.4] - 2026-09-05

### Added

- Public Vercel Blob delivery for installers while the source repository stays private.
- Signed desktop updater artifacts and a public update manifest for macOS, Windows, and Linux.
- Stable landing-page download URLs that follow the latest published release.

### Changed

- Desktop update checks now use the public landing endpoint instead of private GitHub release URLs.

## [0.2.3] - 2026-09-05

### Changed

- Historical release details were not recorded in the repository; the existing published release is preserved.

## [0.2.2] - 2026-09-05

### Added

- Multi-platform release CI/CD and direct landing-page downloads.

### Fixed

- Desktop stability issues addressed in the release commits.

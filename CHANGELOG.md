# Changelog

Every release has a section here, newest first. Add it in the same commit that bumps `VERSION`: the build fails without one, and the section becomes the release notes on Gitea and GitHub.

## [0.3.1] - 2026-11-25

### Added

- This changelog. Each release's notes on Gitea and GitHub now come from its section here.

## [0.3.0] - 2026-11-25

### Added

- The Home Assistant blueprint shows its version in its name (now **v0.3.0**), so you can see when there is an update to re-import.

## [0.2.0] - 2026-11-24

### Added

- Power control is configurable: `POWER_ON_ORDER` (default `ipmi,wol`) and `POWER_OFF_ORDER` (default `api,ipmi`). IPMI is optional; the container uses whichever methods are set up and tries the next one if a method fails.
- Shutdown through the backup box's own TrueNAS API (`BACKUP_TRUENAS_URL`, `BACKUP_TRUENAS_API_KEY`), so a box without IPMI can be shut down cleanly.
- Home Assistant blueprint, replacing the two automations: pick the phone in the UI, record successes, and get warned about missed runs. The README has an Import Blueprint button.
- GitHub workflow: when the repo is mirrored to GitHub, it publishes to ghcr.io and creates the release.

### Changed

- The compose example pulls from ghcr.io by default; `TRUENAS_BACKUP_IMAGE` in `.env` switches it to Gitea. One `.env` holds both the image and the container's settings.
- Supports TrueNAS SCALE 25.10 or later and Home Assistant 2026.11 or later only. `IPMI_INTERFACE` is gone (always `lanplus`).

### Fixed

- GitHub builds and releases a new `VERSION` even when the newest mirrored commit only touches docs.

## [0.1.0] - 2026-11-24

### Added

- First release. A container on the primary TrueNAS that, on a cron schedule (`BACKUP_SCHEDULE`, Mondays at 1:00 AM by default), powers the backup box on over IPMI or Wake-on-LAN, runs the replication tasks through the TrueNAS API, shuts the box down with an IPMI soft power-off, and reports to a Home Assistant webhook.
- Gitea workflow that tests, builds and publishes the image and creates the release.

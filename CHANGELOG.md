# Changelog

## 1.2.1 - 2026-11-07

### Added

- icon.png - 256x256 raster icon (the marketplace refuses SVG storefront icons).
- screenshots.json - three storefront screenshots declared from the repository itself.
- MARKET.md now follows the registry rule: one YAML file at data/plugins/<owner>__<repo>.yml.

### Changed

- All repository URLs point at hong1413/dsh-memory.
- Flat package layout (no subdirectories), matching the published repository.

All notable changes to this project are documented here.

## 1.2.0 — 2026-11-06

### Added

- **Click-to-focus camera**: clicking a star flies the camera to it (yaw/pitch solved from the star's
  position, eased over ~680 ms, shortest angular path); the detail card gains a "back to overview" action.
- **Pending proposals as meteors**: pending proposals render as slow-falling meteors in screen space
  (they do not drift with the camera; the batch freezes while the pointer is inside the canvas).
  Clicking one opens an approval card with **Approve & execute** / **Reject**.
- **Proposal action route**: `POST /dsh-memory/proposals` accepts `{id, action}` for pending proposals
  only; `GET` returns 405, malformed ids 400. Disable with `allowUiWrites: false`.
- `memory_status` reports pending proposals; `starmap.json` now includes `proposals[]`.
- README (English) and README.zh-CN.md, MIT license, changelog, screenshots.

### Changed

- Meteors moved from world space to screen space so they stay clickable at any camera angle.
- Interaction hint shortened; legend and hint no longer overlap.

## 1.1.0 — 2026-11-06

### Added

- Interactive **3D star map** settings page (`settings.section`): zone nebulae, subzone clusters,
  unit stars, depth sorting, perspective projection, orbit camera, auto-rotation, adaptive framing.
- Read-only data route `GET /dsh-memory/starmap.json` backed by `vendor/starmap_dump.py`.
- Client error boundary: a rendering failure shows a stack trace instead of a blank page.

## 1.0.0 — 2026-11-06

### Added

- Five model tools: `memory_search`, `memory_get`, `memory_status`, `memory_save`, `memory_feedback`.
- Approval-gated writes with fail-closed behaviour.
- Bundled single-file engine (`vendor/memory_onefile.py`) with a standalone CLI and 16-assertion self-test.
- Short system-prompt hint describing when to recall and when to save.

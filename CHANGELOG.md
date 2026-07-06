# Changelog

## v1.1

Turned the script into a reliable, double-clickable app for writing FIBA 3x3
game data to `data.xml`.

### Fixed
- **Subscribe flood** — the app used to re-send `subscribe` every 100 ms. Now it
  subscribes **once** and just listens (this is a push feed).
- **Silent failures** — bad or unexpected messages no longer die quietly.

### Added
- **`FIBA3X3.exe`** — double-click to run in a terminal window (no Python needed).
  Keep `config.json` in the same folder. Rebuild anytime with `build_exe.bat`.
- **Live logging** — status is printed to the console and saved to `fiba3x3.log`.
- **Auto-reconnect** — recovers on its own if the connection drops.
- **Auth retry** — if the server rejects the API key, it retries a few times, then
  shows a red **FAILED TO AUTHENTICATE** message.
- **Config check** — if `config.json` still has placeholder values, it tells you
  exactly what to fix instead of crashing.
- **Atomic writes** — `data.xml` is never read half-written.

### Changed
- `data.xml` is now written **the instant** a change arrives (lowest latency).
- Faster, cleaner shutdown on Ctrl+C.

### Notes
- The FIBA feed sends updates **at most every 100 ms** — faster isn't possible
  from the server.
- `apiKey`, `eventId`, and `webSocketUrl` must all come from the **same** Venue
  Server session, or the server rejects the key.

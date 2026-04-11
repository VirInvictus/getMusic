# Lattice — Roadmap

What's done, what's next. Updated as of v3.0.0.

---

## Done

- [x] Library tree generation with artist/album/track/rating display
- [x] Optional genre tags in library tree (`--genres`)
- [x] AI-readable library export (pipe-delimited, token-efficient)
- [x] Genre wings — separate library file per genre
- [x] Library statistics (formats, bitrates, ratings, top artists/genres)
- [x] FLAC integrity verification (`flac -t` or FFmpeg, parallel workers)
- [x] MP3 integrity verification (FFmpeg decode, parallel workers)
- [x] Opus integrity verification (FFmpeg decode, parallel workers)
- [x] Cover art extraction with format priority (FLAC > Opus > M4A > MP3)
- [x] Front cover preference over generic embedded images
- [x] Case-insensitive existing cover detection
- [x] Missing art reporting (no art vs embedded-only distinction)
- [x] Duplicate album detection across directories/formats
- [x] Tag audit for missing title/artist/track/genre
- [x] Full-screen curses TUI with arrow-key navigation
- [x] Color-coded section groups and styled Unicode box drawing
- [x] Curses prompts and pause screens (no raw `input()` breaks)
- [x] Fallback to typed input when curses is unavailable
- [x] Library submenu for tree/AI/wings modes
- [x] Unified tag reader (`get_all_tags` → `TagBundle`, single file open)
- [x] Unified decode scanner (shared across MP3/Opus modes)
- [x] `--dry-run` support for art extraction
- [x] `--version` flag
- [x] Text output format (replaced CSV)

---

## Future

- [ ] **WAV/WMA integrity** — extend the unified decode scanner to cover remaining formats
- [ ] **Art quality audit** — report covers below a resolution threshold (e.g., < 500x500)
- [ ] **Bitrate audit** — flag files below a configurable bitrate floor (e.g., < 192 kbps)
- [ ] **Rating distribution per genre** — cross-tabulate ratings with genre tags in `--stats`
- [ ] **Playlist export** — generate M3U playlists from library tree filters (e.g., 5-star only)
- [ ] **Configurable layout** — support non-standard directory structures via pattern argument
- [ ] **Multi-root scanning** — accept multiple `--root` paths in a single invocation
- [ ] **Progress persistence** — resume interrupted integrity scans from where they left off
- [ ] **Color output in CLI mode** — ANSI color for terminal output outside the TUI

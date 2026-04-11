# Lattice — Application Specification

**Version:** 3.0  
**Language:** Python 3.14+  
**Dependencies:** mutagen (required), tqdm (optional)  
**License:** MIT

---

## 1. Mission Statement

Lattice is a CLI toolkit for music collectors who manage their own libraries
outside of any player's database. It reads tags directly via mutagen —
player-agnostic by design. Every operation works from the filesystem and
embedded metadata, not from a proprietary database or cloud service.

Design philosophy: **one script, every library maintenance task.** No
frameworks, no plugins, no configuration files. The standard collector
layout (`~/Music/ARTIST/ALBUM/01 - Track.flac`) is the only assumption.

---

## 2. Architecture

### 2.1 Single-File Design

The entire toolkit lives in `Lattice.py`. No package structure, no imports
from local modules. This is intentional — the script is meant to be dropped
into any directory and run. External dependencies are limited to `mutagen`
(required for tag reading) and `tqdm` (optional, for progress bars).

### 2.2 Tag Reading

All tag extraction runs through `get_all_tags()`, which returns a `TagBundle`
named tuple from a single `MutagenFile()` open. Format-specific tag field
mapping (ID3 for MP3, VorbisComment for FLAC/Opus/OGG, MP4 atoms for M4A)
is handled internally. Ratings are read from POPM, TXXX, or Vorbis comment
fields — compatible with foobar2000's `foo_quicktag` and most other taggers.

### 2.3 Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.wav` · `.wma` · `.aac`

### 2.4 Interactive TUI

When run with no arguments, the script launches a full-screen curses TUI with:
- Arrow-key navigation with highlighted selection cursor
- Color-coded section groups (Library, Integrity, Artwork, Metadata)
- Styled Unicode box drawing for menus, prompts, and pause screens
- Fallback to typed numbered input if curses is unavailable

### 2.5 CLI Interface

Every mode is accessible via flags (`--library`, `--testFLAC`, etc.) for
scripting and automation. All modes accept `--root`, `--output`, `--workers`,
`--quiet`, and `--verbose` where applicable.

---

## 3. Modes

| Mode | Flag | Description |
|------|------|-------------|
| Library tree | `--library` | Formatted text tree with artist/album/track/rating/genre |
| AI export | `--ai-library` | Token-efficient flat export for LLM recommendation prompts |
| Genre wings | `--all-wings` | Separate library tree file per genre |
| Statistics | `--stats` | Format breakdown, bitrate, ratings, genres, top artists |
| FLAC integrity | `--testFLAC` | Verify via `flac -t` or FFmpeg with parallel workers |
| MP3 integrity | `--testMP3` | Decode via FFmpeg, report errors/warnings |
| Opus integrity | `--testOpus` | Decode via FFmpeg, report errors/warnings |
| Cover extraction | `--extractArt` | Extract embedded art with format priority ranking |
| Missing art | `--missingArt` | Report directories with no cover art |
| Duplicates | `--duplicates` | Detect same artist+album across directories/formats |
| Tag audit | `--auditTags` | Report files missing title, artist, track number, or genre |

---

## 4. Output

All output modes write `.txt` reports (not CSV). Results are grouped by
severity or category with headers and relative paths. Designed for human
reading, not spreadsheet import.

---

## 5. What Lattice Is Not

- **Not a player.** It reads tags — it does not play audio.
- **Not a tagger.** It reads metadata — it does not write it.
- **Not a database.** It walks the filesystem every time — there is no index.
- **Not a sync tool.** It does not interact with cloud services or devices.

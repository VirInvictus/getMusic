<p align="center">
  <img src="logo.svg" alt="getMusic.py" width="420">
</p>
<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://ko-fi.com/vrnvctss"><img src="https://img.shields.io/badge/support-Ko--fi-ff5f5f?logo=kofi" alt="Ko-fi"></a>
</p>

A CLI toolkit for music collectors who manage their own libraries. Builds text-based library trees, verifies file integrity across formats, extracts and audits cover art, detects duplicates, and reports incomplete metadata — all from a single script.

## Why this exists

If you manage a large library (5000+ songs) outside of any particular player's database, you eventually need tools that work the way your library is actually structured. This script expects the standard collector layout:

```
~/Music/ARTIST NAME/ALBUM NAME/01 - Track.flac
```

It reads tags directly via [mutagen](https://mutagen.readthedocs.io/) — player-agnostic by design. Ratings are pulled from standard tag fields (POPM, TXXX, Vorbis comments). I use foobar2000's `foo_quicktag` component with keyboard shortcuts to set `%rating%` between 1 and 5, but any tagger that writes to standard fields will work.

## Sample output

```
ARTIST: Ólafur Arnalds
  ├── ALBUM: Found Songs (Neo-Classical)
      ├── SONG: 01. Ólafur Arnalds — Erla's Waltz (flac) [★★★★★ 5.0/5]
      ├── SONG: 02. Ólafur Arnalds — Raein (flac) [★★★★★ 5.0/5]
      ├── SONG: 03. Ólafur Arnalds — Romance (flac) [★★★★★ 5.0/5]
      ├── SONG: 04. Ólafur Arnalds — Allt varð hljótt (flac) [★★★★★ 5.0/5]
      ├── SONG: 05. Ólafur Arnalds — Lost Song (flac) [★★★★★ 5.0/5]
      ├── SONG: 06. Ólafur Arnalds — Faun (flac) [★★★★★ 5.0/5]
      └── SONG: 07. Ólafur Arnalds — Ljósið (flac) [★★★★★ 5.0/5]
```

Genre tags are optional (`--genres`). If your genre metadata is inconsistent, leave them off — the tree gets unwieldy fast.

## Features

| Mode | Flag | Description |
|------|------|-------------|
| **Library tree** | `--library` | Builds a formatted text tree with artist/album/track/rating/genre |
| **FLAC integrity** | `--testFLAC` | Verifies FLAC files using `flac -t` or FFmpeg, reports failures to text |
| **MP3 integrity** | `--testMP3` | Decodes MP3 files through FFmpeg, reports errors and warnings to text |
| **Opus integrity** | `--testOpus` | Decodes Opus files through FFmpeg, reports errors and warnings to text |
| **Cover art extraction** | `--extractArt` | Extracts embedded art to `cover.jpg` with format priority ranking |
| **Missing art report** | `--missingArt` | Lists directories with no cover art (folder or embedded) to text |
| **Duplicate detection** | `--duplicates` | Finds same artist+album appearing across multiple directories/formats |
| **Tag audit** | `--auditTags` | Reports files missing title, artist, track number, or genre to text |
| **Version** | `--version` | Prints version and exits |

Running with no arguments launches an interactive menu.

## Requirements

**Python packages:**

```
pip install mutagen tqdm
```

`tqdm` is optional — the script falls back to a built-in progress bar if it's not installed.

**System tools (integrity modes):**

- [`flac`](https://xiph.org/flac/) — used by `--testFLAC` (preferred)
- [`ffmpeg`](https://ffmpeg.org/) — used by `--testMP3`, `--testOpus`, and as a fallback for `--testFLAC`

On Windows: `winget install flac ffmpeg`
On Fedora/RHEL: `sudo dnf install flac ffmpeg-free`
On Debian/Ubuntu: `sudo apt install flac ffmpeg`

## Usage

```bash
# Build a library tree with genre tags
python getMusic.py --library --root ~/Music --output library.txt --genres

# Verify FLAC integrity (4 parallel workers)
python getMusic.py --testFLAC --root ~/Music --output flac_errors.txt --workers 4

# Verify MP3s for decode errors
python getMusic.py --testMP3 --root ~/Music --output mp3_errors.txt --workers 4

# Verify Opus files for decode errors
python getMusic.py --testOpus --root ~/Music --output opus_errors.txt --workers 4

# Extract cover art (FLAC > Opus > M4A > MP3 priority)
python getMusic.py --extractArt --root ~/Music

# Preview art extraction without writing files
python getMusic.py --extractArt --root ~/Music --dry-run

# Report directories missing cover art
python getMusic.py --missingArt --root ~/Music --output missing_art.txt

# Find duplicate albums across formats
python getMusic.py --duplicates --root ~/Music --output duplicates.txt

# Audit tags for missing metadata
python getMusic.py --auditTags --root ~/Music --output tag_audit.txt
```

## Cover art extraction

The `--extractArt` mode replaces the old standalone `extract_opus_art.py` and `extract_mp3_art.py` scripts. Key improvements:

- **Format priority** — when a directory contains multiple audio formats, art is extracted from the highest-quality source: FLAC → Opus/OGG → M4A → MP3.
- **Case-insensitive detection** — checks for existing cover files (`cover.jpg`, `folder.jpg`, `front.jpg`, `album.jpg`, and their `.jpeg`/`.png` variants) case-insensitively. No more `cover.jpg` / `Cover.jpg` collisions.
- **Front cover preference** — within each format, prefers the "Front Cover" picture type over generic embedded images.
- **Four format support** — handles FLAC pictures, Opus/OGG `METADATA_BLOCK_PICTURE`, M4A `covr` atoms, and MP3 `APIC` frames.

## Supported formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.wav` · `.wma` · `.aac`

## Full help output

```
usage: getMusic.py [-h] [--version]
                   [--library | --testFLAC | --testMP3 | --testOpus | --extractArt | --missingArt | --duplicates | --auditTags]
                   [--root ROOT] [--output OUTPUT] [--workers WORKERS]
                   [--prefer {flac,ffmpeg}] [--quiet] [--genres] [--dry-run]
                   [--only-errors | --no-only-errors] [--ffmpeg FFMPEG]
                   [--verbose]

Music library toolkit: tree, integrity, art, duplicates, tag audit

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --library             Generate library tree
  --testFLAC            Verify FLAC files
  --testMP3             Verify MP3 files
  --testOpus            Verify Opus files via FFmpeg decode
  --extractArt          Extract embedded cover art to folder
  --missingArt          Report directories missing cover art
  --duplicates          Detect duplicate artist+album across formats
  --auditTags           Report files with incomplete tags
  --root ROOT           Root directory (default: current)
  --output OUTPUT       Output path
  --workers WORKERS     Parallel workers (integrity modes)
  --prefer {flac,ffmpeg}
                        Preferred tool (FLAC mode)
  --quiet               Minimize output
  --genres              Include album genres in library tree
  --dry-run             Preview changes without writing (extractArt)
  --only-errors, --no-only-errors
                        Write only errors/warns (MP3/Opus modes)
  --ffmpeg FFMPEG       Path to ffmpeg
  --verbose             Verbose output
```

## Support

If this saved you time, consider [buying me a coffee](https://ko-fi.com/vrnvctss).

<p align="center">
  <img src="logo.svg" alt="Lattice" width="420">
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
| **AI library export** | `--ai-library` | Token-efficient flat export for LLM recommendation prompts |
| **Genre wings** | `--all-wings` | Generates a separate library tree file for each genre |
| **Library statistics** | `--stats` | Library-wide statistics: format breakdown, bitrate, ratings, genres, top artists |
| **FLAC integrity** | `--testFLAC` | Verifies FLAC files using `flac -t` or FFmpeg, reports failures to text |
| **MP3 integrity** | `--testMP3` | Decodes MP3 files through FFmpeg, reports errors and warnings to text |
| **Opus integrity** | `--testOpus` | Decodes Opus files through FFmpeg, reports errors and warnings to text |
| **Cover art extraction** | `--extractArt` | Extracts embedded art to `cover.jpg` with format priority ranking |
| **Missing art report** | `--missingArt` | Lists directories with no cover art (folder or embedded) to text |
| **Duplicate detection** | `--duplicates` | Finds same artist+album appearing across multiple directories/formats |
| **Tag audit** | `--auditTags` | Reports files missing title, artist, track number, or genre to text |
| **Version** | `--version` | Prints version and exits |

Running with no arguments launches an interactive TUI — a full-screen curses interface with arrow-key navigation, color-coded section groups (Library, Integrity, Artwork, Metadata), and a highlighted selection cursor. Menus, parameter prompts, and pause screens all render inside styled Unicode boxes for a consistent experience. Library tree, AI export, and genre wings live in a dedicated submenu. Falls back to typed input if curses is unavailable.

## Installation & Requirements

Lattice can be installed as a Python package or compiled into a standalone binary.

**Option 1: Install via pipx (Recommended)**
```bash
pipx install .
# Now you can run `lattice` globally
```

**Option 2: Install via pip (Virtual Environment)**
```bash
python -m venv .venv
source .venv/bin/activate
pip install .
```

**Option 3: Compile a standalone binary (PyInstaller)**
```bash
pip install .[pyinstaller] pyinstaller
pyinstaller --onefile --name lattice --paths src src/lattice/__main__.py
# Move the compiled `lattice` binary from the `dist` folder to anywhere in your PATH.
```

**System tools (integrity modes):**

- [`flac`](https://xiph.org/flac/) — used by `--testFLAC` (preferred)
- [`ffmpeg`](https://ffmpeg.org/) — used by `--testMP3`, `--testOpus`, and as a fallback for `--testFLAC`

On Windows: `winget install flac ffmpeg`
On Fedora/RHEL: `sudo dnf install flac ffmpeg-free`
On Debian/Ubuntu: `sudo apt install flac ffmpeg`

## Usage

Lattice now remembers your library location! On your first run, whether via the TUI or the CLI, Lattice will ask for the path to your music library and save it to `~/.config/lattice/config.json`. After that, you no longer need to provide the `--root` argument.

```bash
# Build a library tree with genre tags
lattice --library --output library.txt --genres

# Export library for AI/LLM recommendation prompts
lattice --ai-library --output library_ai.txt

# Generate per-genre library files (one .txt per genre)
lattice --all-wings --output wings/
lattice --all-wings --output wings/ --genres

# Library statistics (prints to screen, or --output for file)
lattice --stats
lattice --stats --output library_stats.txt

# Verify FLAC integrity (4 parallel workers)
lattice --testFLAC --output flac_errors.txt --workers 4

# Verify MP3s for decode errors
lattice --testMP3 --output mp3_errors.txt --workers 4

# Verify Opus files for decode errors
lattice --testOpus --output opus_errors.txt --workers 4

# Extract cover art (FLAC > Opus > M4A > MP3 priority)
lattice --extractArt

# Preview art extraction without writing files
lattice --extractArt --dry-run

# Report directories missing cover art
lattice --missingArt --output missing_art.txt

# Find duplicate albums across formats
lattice --duplicates --output duplicates.txt

# Audit tags for missing metadata
lattice --auditTags --output tag_audit.txt
```

## AI library export

The `--ai-library` mode generates a flat, pipe-delimited summary designed to fit inside an LLM context window for music recommendations:

```
Artist | Album | Genre | Rating | Tracks
--------------------------------------------------
Converge | Jane Doe | Metalcore | 4.8 | 12
Ólafur Arnalds | Found Songs | Neo-Classical | 5.0 | 7
```

**Rating** is the average across all rated tracks. **Tracks** is the number of audio files in the album directory — if you've culled 3-star-and-below tracks from disk, this is your survivor count. Paste the output into a prompt and ask for recommendations against your actual library.

## Genre wings

The `--all-wings` mode scans genre tags across your entire library, groups albums by genre, and writes a separate library tree file for each genre into the output directory — one file per genre, analogous to virtual library wings in Calibre. Useful for breaking a large library into manageable, genre-scoped catalogs.

```bash
python Lattice.py --all-wings --root ~/Music --output wings/
```

Produces files like `Alternative_Rock_Library.txt`, `East_Coast_Rap_Library.txt`, `Neoclassical_Library.txt`, etc. Albums with no genre tag land in `Uncategorized_Library.txt`. Pass `--genres` to include the genre label in each album header.

## Companion Script: `retag.py`

Included in the repository is `retag.py`, a universal genre tagger designed to work directly with the `--all-wings --paths` output. 

Audio metadata formats handle multiple genres entirely differently (ID3 uses null bytes or slashes, Vorbis uses multiple `GENRE=` pairs, Apple uses specific custom atoms). `retag.py` abstracts this container chaos away, allowing you to safely hard-overwrite genres on an entire album directory simultaneously.

**The Workflow:**
1. Generate your wings with paths: `lattice --all-wings --root ~/Music --output wings/ --paths`
   *(If you are using the compiled binary, replace `lattice` with `./dist/lattice`)*
2. Open a generated wing (e.g., `Uncategorized_Library.txt`) and copy the bracketed `[/path/to/album]` from an album header.
3. Pass that path and your desired new genre(s) to `retag.py`:
   ```bash
   ./retag.py "/mnt/SharedData/Music/Kanye West/Yeezus" "Alternative Rap" "Industrial"

## Library statistics

The `--stats` mode produces a full library report: file counts, total size and duration, format breakdown with per-format sizes, bitrate summary (with low-quality flagging), rating distribution with bar charts, top genres, and top artists by track count. Prints to screen by default, or `--output` to save.

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
usage: lattice [-h] [--version]
                   [--library | --ai-library | --all-wings | --testFLAC | --testMP3 | --testOpus | --extractArt | --missingArt | --duplicates | --auditTags | --stats]
                   [--root ROOT] [--output OUTPUT] [--workers WORKERS]
                   [--prefer {flac,ffmpeg}] [--quiet] [--genres] [--dry-run]
                   [--only-errors | --no-only-errors] [--ffmpeg FFMPEG]
                   [--verbose]

Music library toolkit: tree, integrity, art, duplicates, tag audit

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --library             Generate library tree
  --ai-library          Generate token-efficient library for AI recommendations
  --all-wings           Generate separate library files for each genre
  --testFLAC            Verify FLAC files
  --testMP3             Verify MP3 files
  --testOpus            Verify Opus files via FFmpeg decode
  --extractArt          Extract embedded cover art to folder
  --missingArt          Report directories missing cover art
  --duplicates          Detect duplicate artist+album across formats
  --auditTags           Report files with incomplete tags
  --stats               Library-wide statistics summary
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

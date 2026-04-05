# getMusic.py — Patch Notes

## v2.2.0 (2026-04-05)

---

### New Feature: AI-Readable Library Export

**`--ai-library`** generates a flat, token-efficient summary of the music
library for use in LLM recommendation prompts. One line per album in
pipe-delimited format:

```
Artist | Album | Genre | Rating | Tracks
--------------------------------------------------
Converge | Jane Doe | Metalcore | 4.8 | 12
```

- **Rating** is the average of all rated tracks in the album, rounded to one
  decimal. Blank if no tracks are rated.
- **Tracks** is the number of audio files surviving in the album directory —
  the post-cull headcount. An AI reading `5.0 | 1` vs `4.6 | 12` gets the
  density signal without extra framing.
- Genre is sampled from the first track with a genre tag.
- Output defaults to `library_ai.txt`. Available from both CLI and interactive
  menu (option 10).

### Performance

**`get_all_tags` reduced to a single `MutagenFile` open per file.** The v2.1.0
unified reader still opened each file twice — once via the EasyID3 abstraction
pass, once via the full format-specific path (because rating, duration, and
bitrate aren't available through the easy interface). The easy pass is now
eliminated entirely; all tag extraction runs against the single full object.
The MP3 branch also had a separate `ID3(file_path)` call on top of the
`MutagenFile` open — removed, tags are read from `audio.tags` directly.

On a 6,300-track library, this eliminates ~12,600 redundant file opens per
full-library mode.

**`TagBundle` extended with `duration_s` and `bitrate_kbps`.** These fields are
extracted from `audio.info` during the same single open. `run_stats` previously
opened every file a second time just to read duration and bitrate — that
redundant open is gone.

**First-song double-read eliminated in `--library --genres`.** Genre was read
from the first song before the per-track loop, then the loop re-read the same
file. The album header is now deferred until the first track's tags are available
inside the loop.

**`count_audio_files` was called twice in `--ai-library`.** Once for the console
message, once for the progress bar. Now called once, result reused.

**`_has_embedded_art` duplicated `_extract_best_art`'s directory scan logic.**
Collapsed to a one-liner: `return _extract_best_art(directory) is not None`.

**Low-quality bitrate count in `--stats`** used a list comprehension just to
call `len()`. Replaced with a generator sum.

### Bug Fixes

**`--root ~/Music` didn't work from the CLI.** `main()` was missing
`os.path.expanduser()` — tilde expansion only worked in the interactive menu.

**`--library --output subdir/file.txt` crashed.** `write_music_library_tree`
opened the output file directly without creating parent directories, unlike
every other mode. Added `os.makedirs`.

**`_find_files_by_ext_path` matched false extensions.** Used
`filename.endswith('.flac')` which would match a hypothetical file named
`notflac`. Replaced with `os.path.splitext` for exact extension matching.

**Rating bucketing in `--stats` used Python's `round()` (banker's rounding).**
A 4.5 rating rounded to 4, but so did 3.5. Replaced with `int()` (truncate)
for consistent behavior matching the star display logic in `format_rating`.

### Structural Improvements

**Unified MP3/Opus decode scanner.** `_scan_one_mp3`, `_scan_one_opus`,
`run_mp3_mode`, `run_opus_mode`, and `_format_mp3_meta` collapsed into three
shared functions: `_scan_one_file`, `_run_decode_scan`, and `_format_row_meta`.
Format-specific behavior is parameterized via `ext`, `enrich`, and
`ffmpeg_required` flags. Adding a new format is now a three-line wrapper.

**`_FallbackProgress` class replaces all `if pbar:` conditionals.** `_make_pbar`
now always returns an object with `.update()` and `.close()`, whether tqdm is
installed or not. Eliminated 6 conditional blocks and 4 dead counter variables
(`current_file`, `checked`, `scanned_count`, `current`) that existed only to
feed the manual fallback.

**`is_audio()` helper.** Replaced 6 inline
`os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS` patterns. Two callsites
where `ext` was already extracted for other purposes were left as-is.

**`_looks_numeric()` helper.** Replaced 4 inline
`str(val).replace('.', '').isdigit()` patterns in rating extraction code.

**`_prompt_path()` helper.** Consolidated 8 identical
`os.path.abspath(os.path.expanduser(_prompt_str(...)))` patterns in the
interactive menu.

**`test_flac` simplified.** Two mirrored if/elif branches (one per tool
preference) collapsed into a priority-ordered tool list with a single loop.

### Dead Code Removed

**Four standalone tag functions removed (~190 lines).** `get_title_artist_track`,
`get_album`, `get_genre`, `get_rating` — all superseded by `get_all_tags` in
v2.1.0 but left in the codebase. No internal callers remained.

**`_get_cover_file_path`** — defined but never called by any mode.

**`_scan_one_mp3`, `_scan_one_opus`, `_format_mp3_meta`** — replaced by the
unified scanner.

**`ID3` and `ID3NoHeaderError` imports** — no longer needed after the MP3
branch was rewritten to use `audio.tags` from `MutagenFile`.

**Stale `(NEW)` markers** removed from section headers.

---

## v2.1.0 (2026-04-04)

---

### Bug Fixes

**Opus mode wrote to `mp3_scan_results.csv`.** Copy-paste bug in `_write_header`
hardcoded `DEFAULT_MP3_OUTPUT` as the fallback filename for all modes. Opus scans
silently wrote results to the wrong file.

**`_extract_art_from_mp3` called `MUTAGEN_MP3` without checking
`HAVE_MUTAGEN_MP3`.** If `mutagen` installed but `mutagen.mp3` failed to import,
art extraction threw `NameError`.

**`verbose` flag mutated `only_errors` and `quiet` inside the MP3 scan loop.**
Dead writes on every iteration after the first. Moved above the loop. Opus mode
now has the same `verbose` behavior for consistency.

**Terminal corruption after subprocess modes.** Running FLAC/MP3/Opus integrity
checks from the interactive menu left the terminal with `icrnl` disabled — Enter
sent `^M` instead of newline, and input froze. Caused by `run_proc` using raw
bytes mode while `flac -t` wrote binary diagnostic data to stderr, colliding
with tqdm's cursor manipulation. Fixed with `_reset_terminal()` (`stty sane`)
called at the top of every menu loop, and in a `finally` block on CLI exit.

### Structural Improvements

**Unified tag reader: `get_all_tags()` → `TagBundle`.** The old code opened each
file up to 4× via independent `MutagenFile()` calls (`get_title_artist_track`,
`get_album`, `get_genre`, `get_rating`). Consolidated into a single function
returning a `TagBundle` named tuple. ~19,000 fewer file opens per `--library`
run on a 6,300-track library. Callers updated: `write_music_library_tree`,
`run_tag_audit`, `run_duplicates`. Original standalone functions preserved for
any external imports but no longer called internally.

**Duplicate file-finder eliminated.** `find_files_by_ext` (string generator) and
`_find_files_by_ext_path` (Path list) did the same job. Removed the former,
pointed FLAC mode at the latter.

**Vestigial `paths` list removed from `run_mp3_mode`.** Leftover from a
multi-root design that never shipped. Replaced with a plain `root_path`.

**`Counter` import consolidated.** Was at module level via `defaultdict` but then
re-imported locally in `run_tag_audit`. One import, one location.

**Removed unused `Iterable` from typing imports.**

### Output Format: CSV → Formatted Text

All output modes now write `.txt` reports instead of `.csv`. None of these
outputs were destined for spreadsheets — they're checklists and diagnostics
read by one person, and the format now respects that.

- **FLAC/MP3/Opus integrity** — Header with scan totals, results grouped by
  severity (ERRORS → WARNINGS → OK). Relative paths, tool/error details,
  compact metadata where relevant (bitrate, sample rate, duration).
- **Missing art** — Two sections: no art at all, embedded only. Relative paths
  with file counts.
- **Duplicates** — Grouped by artist/album pair with directories nested
  underneath showing format sets.
- **Tag audit** — Grouped by directory, each file showing format and missing
  fields. Header includes field-level breakdown counts.

### Dead Code Removed

**`_write_header` / `_close_writer` / `_rotated_path`** — Entire CSV writer
infrastructure gone. These managed `csv.DictWriter` lifecycle via a
monkey-patched `_file_handle` attribute. With text output, file writes are
straightforward `open()` calls.

**`import csv`** — No longer imported.

---

## v2.0.0 (2026-03-15)

---

getMusic.py is now a single unified toolkit. The standalone
`extract_opus_art.py` and `extract_mp3_art.py` scripts are retired — their
functionality lives in the main script as `--extractArt`, with improvements.

### New Modes

- **`--testOpus`** — Opus file integrity checking via FFmpeg decode (same
  pattern as `--testMP3`).
- **`--extractArt`** — Extract embedded cover art to `cover.jpg` with format
  priority ranking (FLAC > Opus > M4A > MP3) and `--dry-run` support.
- **`--missingArt`** — Report directories with no cover art (distinguishes
  "no art at all" from "embedded only").
- **`--duplicates`** — Detect same artist+album appearing across multiple
  directories or formats.
- **`--auditTags`** — Report files missing title, artist, track number, or
  genre with a summary breakdown.

### Bug Fixes

- **Fixed cover.jpg collision** — Cover detection is now case-insensitive.
  Running art extraction in a folder with both Opus and MP3 files no longer
  produces both `cover.jpg` and `Cover.jpg`.

### Improvements

- Art extraction prefers front cover (type 3) over generic embedded images.
- Art extraction supports four formats: FLAC, Opus/OGG, M4A, MP3.
- Interactive menu updated with all eight modes.
- All existing CLI invocations remain backward-compatible.

### Removed

- `extract_opus_art.py` (folded into `--extractArt`).
- `extract_mp3_art.py` (folded into `--extractArt`).

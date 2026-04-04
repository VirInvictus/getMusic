# getMusic.py — Patch Notes

**Date:** 2026-04-04

---

## Bugs Fixed

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

## Structural Improvements

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

## Output Format: CSV → Formatted Text

All output modes now write `.txt` reports instead of `.csv`. None of these
outputs were destined for spreadsheets — they're checklists and diagnostics
read by one person, and the format now respects that.

- **FLAC/MP3/Opus integrity** — Header with scan totals, results grouped by
  severity (ERRORS → WARNINGS → OK). Relative paths, tool/error details,
  compact metadata where relevant (bitrate, sample rate, duration).
- **Missing art** — Two sections: no art at all, embedded only. Relative paths
  with file counts.
- **Duplicates** — Grouped by artist/album pair with directories nested
  underneath showing format sets. No more repeated artist/album on flat rows.
- **Tag audit** — Grouped by directory, each file showing format and missing
  fields. Header includes field-level breakdown counts.

## Dead Code Removed

**`_write_header` / `_close_writer` / `_rotated_path`** — Entire CSV writer
infrastructure gone. These managed `csv.DictWriter` lifecycle via a
monkey-patched `_file_handle` attribute. With text output, file writes are
straightforward `open()` calls. No more leaked handles, no more
`type: ignore` comments.

**`import csv`** — No longer imported. Zero CSV references remain.

## Interactive Menu

All prompts updated from "CSV output file" to "Output file".

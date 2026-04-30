# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Lattice is a CLI/TUI toolkit for music libraries that treats the filesystem as the source of truth. It reads tags through `mutagen`, walks the tree on every invocation (no index/database), and writes plain `.txt` reports. Python 3.9+, packaged with Hatch (`pyproject.toml`). Runtime deps: `mutagen`, `tqdm`. Current version is `4.3.4` (kept in `src/lattice/config.py`).

The home directory `CLAUDE.md` at `~/CLAUDE.md` covers system-wide conventions (zsh aliases, package managers, dotfiles repo). This file overrides it for work scoped to Lattice.

## Common commands

```bash
# Run from a checkout (without installing)
python -m lattice [flags]              # entrypoint: src/lattice/__main__.py

# Install editable into the active venv (uv-friendly)
pip install -e .

# Install as a user-global CLI
pipx install .                         # exposes `lattice`

# Standalone binary (PyInstaller, declared in pyproject.toml)
hatch run build-bin                    # → dist/lattice

# Launch interactive TUI (no args)
lattice

# Most-used modes
lattice --library --output library.txt --genres
lattice --testFLAC --workers 4
lattice --extractArt --dry-run
lattice --stats
```

There is **no test suite**. The README references `python3 -m unittest discover src/lattice/test`, but `src/lattice/test/` does not exist — treat the README claim as aspirational. Don't fabricate tests unless asked.

System tools used by integrity modes: `flac` (preferred for `--testFLAC`) and `ffmpeg` (everything else, plus FLAC fallback). Both should already be on Fedora via `dnf`.

## Architecture

Layer-based package under `src/lattice/`:

```
cli.py     argparse + dispatch — every mode flag maps to one function call
tui.py     curses full-screen menu shown when invoked with no args
tags.py    get_all_tags() → TagBundle — single MutagenFile() open per file
config.py  VERSION, DEFAULT_*_OUTPUT names, AUDIO_EXTENSIONS, COVER_NAMES,
           and persistent library root in ~/.config/lattice/config.json
utils.py   filesystem walk, progress bar factory (_make_pbar), subprocess
           helper, layout parser, terminal reset
modes/     one file per mode group: library, integrity, artwork, audit,
           stats, playlists. Each exports run_* / write_* functions.
```

Both `cli.py` and `tui.py` call into `modes/*` directly. The TUI is not a wrapper around the CLI parser — it builds the same kwargs and invokes the same mode functions. Keep them in sync when adding a flag: argparse entry in `cli.py`, dispatch branch in `cli.py:main`, and a corresponding TUI menu entry in `tui.py`.

`tags.py` is the only place that knows about per-format tag layouts (ID3 frames, Vorbis comments, MP4 atoms, ASF). All other code consumes a `TagBundle`. Ratings are normalized to a 0–5 float in `utils.normalize_rating`; format-specific rating sources (POPM, TXXX, Vorbis `RATING/SCORE/STARS`, MP4 `*rate*`) are decoded inside `get_all_tags`.

Mode functions accept `root`, `output`, `quiet`, and (where relevant) `workers`, `verbose`, `dry_run`, `layout`. Output paths default to constants in `config.py` — prefer those over hardcoding. Integrity scanners use `ThreadPoolExecutor` with `--workers` (default 4).

Progress reporting goes through `utils._make_pbar`, which dispatches between three implementations: `_TUIPbar` (when `utils.IN_TUI` is set by the TUI before invoking a mode), `tqdm` (when installed and not quiet), and `_FallbackProgress` (plain stdout). New modes should call `_make_pbar`, not `tqdm` directly.

`AUDIO_EXTENSIONS` in `config.py` is the canonical set of supported formats — extend it there, not inline. `COVER_NAMES` is matched case-insensitively (see `utils._has_cover_file`); add new variants there.

## Companion script

`retag.py` at the repo root is a standalone genre-rewriter, **not** part of the `lattice` package and not exposed through the CLI. It's intentionally separate because it writes tags (Lattice itself is read-only — see spec.md §5). Don't fold it into the package without asking.

## Conventions for this repo

- Lattice is read-only by design: it reads tags, decodes audio, and writes reports/playlists/extracted art. It does not write metadata back to audio files. New modes should respect that boundary.
- One file per mode group under `modes/`. Adding a brand-new operation usually means a new function in an existing mode file, not a new file.
- Keep `pyproject.toml`, `spec.md` §1 header, and `config.VERSION` in lockstep on a release. `patchnotes.md` and `roadmap.md` are hand-curated; update them when the user asks.
- Default to no comments — `tags.py` has a few because the format quirks aren't obvious from the code, and that's the bar.
- The standard layout assumption is `ARTIST/ALBUM/Track.ext`, but `--layout` (default `{artist}/{album}`) lets callers override it via `utils.parse_layout`. Don't hardcode `os.sep`-counting logic — use `parse_layout`.

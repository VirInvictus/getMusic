#!/usr/bin/env python3
# filepath: getMusic.py
"""
Unified music library toolkit.

Modes:
  --library       Build a text tree of your music library
  --testFLAC      Verify FLAC file integrity
  --testMP3       Verify MP3 files via FFmpeg decode
  --testOpus      Verify Opus files via FFmpeg decode
  --extractArt    Extract embedded cover art to folder (cover.jpg)
  --missingArt    Report directories with no cover art (folder or embedded)
  --duplicates    Detect same artist+album across formats
  --auditTags     Report files missing title/artist/track/genre
  --stats         Library-wide statistics summary
  --ai-library    Token-efficient library export for AI recommendation prompts
  --all-wings     Generate separate library files for each genre

Usage examples:
  python getMusic.py --library --root ~/Music --output library.txt --genres
  python getMusic.py --ai-library --root ~/Music --output library_ai.txt
  python getMusic.py --all-wings --root ~/Music --output wings/
  python getMusic.py --all-wings --root ~/Music --output wings/ --genres --paths
  python getMusic.py --testFLAC --root ~/Music --output flac_errors.txt --workers 4
  python getMusic.py --testMP3 --root ~/Music --output mp3_errors.txt --workers 4
  python getMusic.py --testOpus --root ~/Music --output opus_errors.txt --workers 4
  python getMusic.py --extractArt --root ~/Music
  python getMusic.py --missingArt --root ~/Music --output missing_art.txt
  python getMusic.py --duplicates --root ~/Music --output duplicates.txt
  python getMusic.py --auditTags --root ~/Music --output tag_audit.txt
  python getMusic.py --stats --root ~/Music
  python getMusic.py --stats --root ~/Music --output library_stats.txt

Notes:
  - Supports: .mp3, .flac, .ogg, .opus, .m4a, .wav, .wma, .aac
  - Art extraction priority: FLAC > Opus > M4A > MP3 (highest likely quality first)
  - Cover detection is case-insensitive to prevent cover.jpg / Cover.jpg collisions
  - Run with no arguments for interactive menu
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple, Tuple, List, Optional, Dict, Any

try:
    import curses
    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False


class TagBundle(NamedTuple):
    """All metadata we care about, extracted once per file."""
    title: Optional[str] = None
    artist: Optional[str] = None
    trackno: Optional[int] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[float] = None
    duration_s: Optional[float] = None
    bitrate_kbps: Optional[int] = None

# --- Mutagen imports ---
HAVE_MUTAGEN_BASE = False
try:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    from mutagen.asf import ASF

    try:
        from mutagen.oggopus import OggOpus
    except ImportError:
        class OggOpus: pass  # type: ignore[no-redef]

    HAVE_MUTAGEN_BASE = True
except ImportError:
    pass

try:
    from mutagen.mp3 import MP3 as MUTAGEN_MP3
    HAVE_MUTAGEN_MP3 = True
except ImportError:
    HAVE_MUTAGEN_MP3 = False

try:
    from tqdm import tqdm
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False

# =====================================
# Constants
# =====================================
VERSION = "3.1.0"

DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.txt"
DEFAULT_MP3_OUTPUT = "mp3_scan_results.txt"
DEFAULT_OPUS_OUTPUT = "opus_scan_results.txt"
DEFAULT_MISSING_ART_OUTPUT = "missing_art.txt"
DEFAULT_DUPLICATES_OUTPUT = "duplicates.txt"
DEFAULT_TAG_AUDIT_OUTPUT = "tag_audit.txt"

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.wav', '.wma', '.aac'}


def is_audio(filename: str) -> bool:
    """Check if a filename has a recognized audio extension."""
    return os.path.splitext(filename)[1].lower() in AUDIO_EXTENSIONS

# Cover filenames to check (case-insensitive matching applied at check time)
COVER_NAMES = {"cover.jpg", "cover.jpeg", "cover.png",
               "folder.jpg", "folder.jpeg", "folder.png",
               "front.jpg", "front.jpeg", "front.png",
               "album.jpg", "album.jpeg", "album.png"}

# Art extraction format priority: highest likely quality first.
# FLAC embeds are typically uncompressed or high-quality;
# Opus/OGG carry FLAC Picture blocks; M4A uses covr atoms; MP3 uses APIC.
ART_FORMAT_PRIORITY = ['.flac', '.opus', '.ogg', '.m4a', '.mp3']

RE_CLEAN_PREFIX = re.compile(r'^[^\-\d]*-\s*')
RE_CLEAN_PATTERNS = [
    re.compile(r'^(?:\d+\s*[-–—]\s*)?(\d+)\.?\s*[-–—]?\s*(.+)$'),
    re.compile(r'^[Tt]rack\s*(\d+)\.?\s*[-–—]?\s*(.+)$'),
    re.compile(r'^(\d+)\s+(.+)$')
]


# =====================================
# Shared utilities
# =====================================

def _reset_terminal() -> None:
    """Restore sane terminal state after subprocess runs.

    Subprocesses (flac, ffmpeg) in raw-bytes mode can corrupt the terminal's
    line discipline — most commonly turning off icrnl so Enter sends \\r
    (displayed as ^M) instead of \\n.  This resets to sane defaults.
    """
    if not sys.stdin.isatty():
        return
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
    except Exception:
        pass


def clean_song_name(filename: str) -> str:
    name_without_ext = os.path.splitext(filename)[0]
    name_without_ext = RE_CLEAN_PREFIX.sub('', name_without_ext)
    for pattern in RE_CLEAN_PATTERNS:
        match = pattern.match(name_without_ext.strip())
        if match:
            track_num = match.group(1).zfill(2)
            title = match.group(2).strip()
            return f"{track_num}. {title}"
    return name_without_ext.strip()


def normalize_rating(val) -> Optional[float]:
    """Normalizes various rating scales (0-100, 0-255, 0-5) to a float 0-5."""
    try:
        val = float(val)
        if val <= 5:
            return val
        elif val <= 10:
            return val / 2.0
        elif val <= 100:
            return val / 20.0
        elif val <= 255:
            return (val / 255.0) * 5.0
    except (ValueError, TypeError):
        pass
    return None


def _looks_numeric(val) -> bool:
    """Check if a value looks like a number (int or float)."""
    return bool(val) and str(val).replace('.', '').isdigit()


def format_rating(rating: Optional[float]) -> str:
    if rating is None:
        return ""
    full_stars = int(rating)
    half_star = rating - full_stars >= 0.5
    empty_stars = 5 - full_stars - (1 if half_star else 0)
    stars = "★" * full_stars
    if half_star:
        stars += "☆"
    stars += "☆" * empty_stars
    return f" [{stars} {rating:.1f}/5]"


def update_progress(current: int, total: int, prefix: str = "Progress") -> None:
    if total == 0:
        return
    percent = (current / total) * 100
    bar_length = 40
    filled_length = int(bar_length * current // total)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    sys.stdout.write(f'\r{prefix}: |{bar}| {current}/{total} ({percent:.1f}%)')
    sys.stdout.flush()
    if current == total:
        print()


def count_audio_files(root_dir: str) -> int:
    total = 0
    for _, _, files in os.walk(root_dir):
        total += sum(1 for f in files if is_audio(f))
    return total


def _first_text(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    try:
        if hasattr(val, "value"):
            val = val.value
    except Exception:
        pass
    return str(val).strip() if val is not None else None


def _parse_track_number(val) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, list) and val:
        if isinstance(val[0], tuple):
            try:
                num = int(val[0][0])
                return num if num > 0 else None
            except (ValueError, IndexError):
                return None
    s = _first_text(val)
    if not s:
        return None
    s = s.split('/')[0]
    try:
        n = int(s)
        return n if n > 0 else None
    except ValueError:
        return None


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", errors="replace")


def run_proc(args: List[str]) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, env=env,
    )
    try:
        out_b, err_b = proc.communicate()
    except KeyboardInterrupt:
        try:
            proc.kill()
        finally:
            proc.wait()
        raise
    return proc.returncode, _decode_bytes(out_b).strip(), _decode_bytes(err_b).strip()


def has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _has_cover_file(directory: str) -> bool:
    """Case-insensitive check for existing cover art files in a directory."""
    try:
        existing = {f.lower() for f in os.listdir(directory)}
    except OSError:
        return False
    return bool(existing & COVER_NAMES)


class _FallbackProgress:
    """Simple progress bar for when tqdm is not installed."""
    __slots__ = ('_current', '_total', '_desc', '_quiet')

    def __init__(self, total: int, desc: str, quiet: bool):
        self._current = 0
        self._total = total
        self._desc = desc
        self._quiet = quiet

    def update(self, n: int = 1) -> None:
        self._current += n
        if not self._quiet:
            update_progress(self._current, self._total, self._desc)

    def close(self) -> None:
        pass


def _make_pbar(total: int, desc: str, quiet: bool):
    """Create a progress bar — tqdm if available, else a simple fallback."""
    if HAVE_TQDM and not quiet:
        return tqdm(total=total, unit="file", desc=desc, dynamic_ncols=True)
    return _FallbackProgress(total, desc, quiet)


# =====================================
# Tag extraction
# =====================================

def get_all_tags(file_path: str) -> TagBundle:
    """Extract all metadata in a single file open."""
    if not HAVE_MUTAGEN_BASE:
        return TagBundle()

    title = artist = album = genre = None
    trackno: Optional[int] = None
    rating: Optional[float] = None
    duration_s: Optional[float] = None
    bitrate_kbps: Optional[int] = None

    try:
        audio = MutagenFile(file_path)
        if not audio:
            return TagBundle()

        # Extract duration and bitrate from audio.info
        info = getattr(audio, 'info', None)
        if info:
            length = getattr(info, 'length', 0.0) or 0.0
            if length > 0:
                duration_s = round(length, 3)
            br = getattr(info, 'bitrate', 0) or 0
            if br > 0:
                bitrate_kbps = int(br / 1000)

        ext = os.path.splitext(file_path)[1].lower()
        tags = getattr(audio, 'tags', {}) or {}

        if ext == '.mp3':
            # ID3 tags — accessible via audio.tags from MutagenFile
            if not tags:
                return TagBundle(title, artist, trackno, album, genre, rating,
                                 duration_s, bitrate_kbps)

            if hasattr(tags, 'get'):
                tit2 = tags.get('TIT2')
                if tit2:
                    title = _first_text(tit2.text)
                tpe1 = tags.get('TPE1')
                tpe2 = tags.get('TPE2')
                if tpe2:
                    artist = _first_text(tpe2.text)
                elif tpe1:
                    artist = _first_text(tpe1.text)
                trck = tags.get('TRCK')
                if trck:
                    trackno = _parse_track_number(trck.text)
                talb = tags.get('TALB')
                if talb:
                    album = _first_text(talb.text)

            if hasattr(tags, 'getall'):
                tcon = tags.getall('TCON')
                if tcon:
                    genre = _first_text(tcon[0])

                # Rating: POPM (prefer WMP, then any) / TXXX
                for popm in tags.getall('POPM'):
                    if getattr(popm, 'email', '') == 'Windows Media Player 9 Series':
                        wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                        rating = wmp_map.get(popm.rating, normalize_rating(popm.rating))
                        break
                if rating is None:
                    for popm in tags.getall('POPM'):
                        if popm.rating > 0:
                            rating = normalize_rating(popm.rating)
                            break
                if rating is None:
                    for txxx in tags.getall('TXXX'):
                        desc = (txxx.desc or "").lower()
                        if 'rating' in desc or desc in ('rate', 'score', 'stars'):
                            val = txxx.text[0] if txxx.text else None
                            if _looks_numeric(val):
                                rating = normalize_rating(val)
                                break

        elif isinstance(audio, MP4):
            title = _first_text(tags.get('\xa9nam'))
            artist = _first_text(tags.get('aART')) or _first_text(tags.get('\xa9ART'))
            trackno = _parse_track_number(tags.get('trkn'))
            album = _first_text(tags.get('\xa9alb'))
            for k in ('\xa9gen', 'gnre'):
                v = tags.get(k)
                if v:
                    genre = _first_text(v)
                    break
            for k, v in tags.items():
                kl = k.lower() if isinstance(k, str) else str(k).lower()
                if 'rate' in kl or 'rating' in kl:
                    v = v[0] if isinstance(v, list) else v
                    if _looks_numeric(v):
                        rating = normalize_rating(v)
                        break

        elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            keys = {k.lower(): k for k in tags.keys()}
            if 'title' in keys:
                title = _first_text(tags[keys['title']])
            if 'albumartist' in keys:
                artist = _first_text(tags[keys['albumartist']])
            elif 'artist' in keys:
                artist = _first_text(tags[keys['artist']])
            if 'tracknumber' in keys:
                trackno = _parse_track_number(tags[keys['tracknumber']])
            if 'album' in keys:
                album = _first_text(tags[keys['album']])
            if 'genre' in keys:
                genre = _first_text(tags[keys['genre']])
            for key, val in tags.items():
                if 'rating' in key.lower() or 'score' in key.lower() or 'stars' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if _looks_numeric(val):
                        rating = normalize_rating(val)
                        break

        elif isinstance(audio, ASF):
            name_map = {k.lower(): k for k in tags.keys()}
            if (k := name_map.get('title')):
                title = _first_text(tags.get(k))
            if (k := name_map.get('wm/albumartist') or name_map.get('author')):
                artist = _first_text(tags.get(k))
            if (k := name_map.get('wm/tracknumber') or name_map.get('tracknumber')):
                trackno = _parse_track_number(tags.get(k))
            if (k := name_map.get('wm/albumtitle')):
                album = _first_text(tags.get(k))
            if (k := name_map.get('wm/genre')):
                genre = _first_text(tags.get(k))
            for key, val in tags.items():
                if 'rating' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if _looks_numeric(val):
                        rating = normalize_rating(val)
                        break

        # Fallback: generic tag iteration for album/genre if still missing
        if album is None or genre is None:
            for k, v in tags.items():
                kl = str(k).lower()
                if album is None and kl == 'album':
                    album = _first_text(v)
                if genre is None and kl in ('genre', 'wm/genre'):
                    genre = _first_text(v)
            if album is None and hasattr(tags, 'getall'):
                talb = tags.getall('TALB')
                if talb:
                    album = _first_text(talb[0])

    except Exception:
        pass

    return TagBundle(title, artist, trackno, album, genre, rating,
                     duration_s, bitrate_kbps)


# =====================================
# Mode: Library tree
# =====================================

def write_music_library_tree(root_dir: str, output_file: str, *, quiet: bool = False, show_genre: bool = False) -> None:
    root_dir = os.path.abspath(root_dir)
    total_files = count_audio_files(root_dir)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {root_dir}\n")

    pbar = _make_pbar(total_files, "Scanning library", quiet)

    output_file = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            for artist_dir in sorted(os.listdir(root_dir)):
                artist_path = os.path.join(root_dir, artist_dir)
                if not os.path.isdir(artist_path):
                    continue

                f.write(f"ARTIST: {artist_dir}\n")
                albums = sorted([
                    alb for alb in os.listdir(artist_path)
                    if os.path.isdir(os.path.join(artist_path, alb))
                ])

                if not albums:
                    f.write("  └── [No Albums Found]\n\n")
                    continue

                for i, album in enumerate(albums):
                    album_path = os.path.join(artist_path, album)
                    connector = "└──" if i == len(albums) - 1 else "├──"

                    songs = sorted([
                        s for s in os.listdir(album_path)
                        if is_audio(s)
                    ])

                    if not songs:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        f.write("      └── [No Audio Files Found]\n")
                        continue

                    # Genre header: defer until first song read, or write immediately
                    if show_genre:
                        album_header_written = False
                    else:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        album_header_written = True

                    for j, song in enumerate(songs):
                        pbar.update(1)

                        song_path = os.path.join(album_path, song)
                        t = get_all_tags(song_path)

                        # Write album header with genre from first track
                        if not album_header_written:
                            genre_str = f" ({t.genre})" if t.genre else ""
                            f.write(f"  {connector} ALBUM: {album}{genre_str}\n")
                            album_header_written = True

                        if t.title or t.artist:
                            parts: List[str] = []
                            if t.trackno:
                                parts.append(f"{int(t.trackno):02d}.")
                            if t.artist:
                                parts.append(t.artist)
                            if t.title:
                                if t.artist:
                                    parts.append("—")
                                parts.append(t.title)
                            display_name = " ".join(parts).strip()
                        else:
                            display_name = clean_song_name(song)

                        ext = os.path.splitext(song)[1].lower().strip('.')
                        rating_str = format_rating(t.rating)

                        song_connector = "└──" if j == len(songs) - 1 else "├──"
                        f.write(f"      {song_connector} SONG: {display_name} ({ext}){rating_str}\n")
                    f.write("\n")
    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Library scan cancelled.")
        return
    finally:
        pbar.close()


# =====================================
# Mode: AI-readable library export
# =====================================
DEFAULT_AI_LIBRARY_OUTPUT = "library_ai.txt"


def write_ai_library(root_dir: str, output_file: str, *, quiet: bool = False) -> None:
    """Write a flat, token-efficient library summary for LLM consumption.

    One line per album: Artist | Album | Genre | Rating | Tracks
    Rating is the average of all rated tracks in the album, or blank if unrated.
    Tracks is the number of audio files surviving in the album directory.
    Genre is sampled from the first track with a genre tag.
    """
    root_dir = os.path.abspath(root_dir)
    total = count_audio_files(root_dir)

    if not quiet:
        print(f"Scanning {total} files under: {root_dir}")

    pbar = _make_pbar(total, "Building AI library", quiet)

    # (artist, album, genre, rating, track_count)
    albums: List[Tuple[str, str, str, str, int]] = []

    for artist_dir in sorted(os.listdir(root_dir)):
        artist_path = os.path.join(root_dir, artist_dir)
        if not os.path.isdir(artist_path):
            continue

        for album_name in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album_name)
            if not os.path.isdir(album_path):
                continue

            songs = [
                s for s in os.listdir(album_path)
                if is_audio(s)
            ]
            if not songs:
                continue

            # Scan all tracks for ratings; sample first for genre
            album_genre = ""
            album_artist = artist_dir
            ratings: List[float] = []

            for song in songs:
                song_path = os.path.join(album_path, song)
                t = get_all_tags(song_path)

                if not album_genre and t.genre:
                    album_genre = t.genre
                if t.rating is not None:
                    ratings.append(t.rating)

                pbar.update(1)

            # Average rating, rounded to one decimal
            if ratings:
                avg = sum(ratings) / len(ratings)
                rating_str = f"{avg:.1f}"
            else:
                rating_str = ""

            albums.append((album_artist, album_name, album_genre, rating_str, len(songs)))

    pbar.close()

    out_path = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("Artist | Album | Genre | Rating | Tracks\n")
        f.write("-" * 50 + "\n")
        for artist, album, genre, rating, tracks in albums:
            f.write(f"{artist} | {album} | {genre} | {rating} | {tracks}\n")

    if not quiet:
        rated = sum(1 for _, _, _, r, _ in albums if r)
        print(f"\nWrote {len(albums)} albums ({rated} rated) to: {out_path}")


# =====================================
# Mode: All wings (genre-based library files)
# =====================================

def _scan_genres(root_dir: str, quiet: bool = False) -> Dict[str, List[Tuple[str, str]]]:
    """Scan the library and group (artist_dir, album_dir) pairs by genre.

    Returns a dict mapping genre name to a sorted list of (artist_dir, album_dir).
    Albums whose tracks have no genre tag are collected under "Uncategorized".
    """
    total = count_audio_files(root_dir)
    if not quiet:
        print(f"Scanning {total} files for genre tags...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    # genre -> set of (artist_dir, album_dir)
    genre_map: Dict[str, set] = defaultdict(set)

    for artist_dir in sorted(os.listdir(root_dir)):
        artist_path = os.path.join(root_dir, artist_dir)
        if not os.path.isdir(artist_path):
            continue

        for album_dir in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album_dir)
            if not os.path.isdir(album_path):
                continue

            songs = [s for s in os.listdir(album_path) if is_audio(s)]
            album_genre = ""
            for song in songs:
                t = get_all_tags(os.path.join(album_path, song))
                pbar.update(1)
                if not album_genre and t.genre:
                    album_genre = t.genre

            genre_map[album_genre or "Uncategorized"].add((artist_dir, album_dir))

    pbar.close()

    # Convert sets to sorted lists
    return {g: sorted(pairs) for g, pairs in genre_map.items()}


def write_all_wings(root_dir: str, outdir: str, *, quiet: bool = False,
                    show_genre: bool = False, show_paths: bool = False) -> int:
    """Generate a separate library tree file for each genre.

    Scans the entire library (root/Artist/Album/songs) to determine each
    album's genre from its tags, then writes one text file per genre into
    *outdir* — analogous to virtual-library wings in Calibre.
    """
    root_dir = os.path.abspath(root_dir)
    genre_groups = _scan_genres(root_dir, quiet=quiet)

    if not genre_groups:
        print("No albums found under root.", file=sys.stderr)
        return 1

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(genre_groups)} genres. Writing wings...\n")

    for genre_name in sorted(genre_groups):
        pairs = genre_groups[genre_name]
        safe_name = re.sub(r'[^\w\s-]', '', genre_name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_Library.txt")

        if not quiet:
            print(f"→ {genre_name} ({len(pairs)} albums)")

        with open(output, 'w', encoding='utf-8') as f:
            # Group albums by artist
            artist_albums: Dict[str, List[str]] = defaultdict(list)
            for artist_dir, album_dir in pairs:
                artist_albums[artist_dir].append(album_dir)

            for artist_dir in sorted(artist_albums):
                f.write(f"ARTIST: {artist_dir}\n")
                albums = sorted(artist_albums[artist_dir])

                for i, album in enumerate(albums):
                    album_path = os.path.join(root_dir, artist_dir, album)
                    connector = "└──" if i == len(albums) - 1 else "├──"

                    songs = sorted([s for s in os.listdir(album_path) if is_audio(s)])

                    if not songs:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        f.write("      └── [No Audio Files Found]\n")
                        continue

                    genre_str = ""
                    if show_genre:
                        first_tag = get_all_tags(os.path.join(album_path, songs[0]))
                        if first_tag.genre:
                            genre_str = f" ({first_tag.genre})"
                    
                    path_str = f" [{album_path}]" if show_paths else ""
                    f.write(f"  {connector} ALBUM: {album}{genre_str}{path_str}\n")

                    for j, song in enumerate(songs):
                        song_path = os.path.join(album_path, song)
                        t = get_all_tags(song_path)

                        if t.title or t.artist:
                            parts: List[str] = []
                            if t.trackno:
                                parts.append(f"{int(t.trackno):02d}.")
                            if t.artist:
                                parts.append(t.artist)
                            if t.title:
                                if t.artist:
                                    parts.append("—")
                                parts.append(t.title)
                            display_name = " ".join(parts).strip()
                        else:
                            display_name = clean_song_name(song)

                        ext = os.path.splitext(song)[1].lower().strip('.')
                        rating_str = format_rating(t.rating)
                        song_connector = "└──" if j == len(songs) - 1 else "├──"
                        f.write(f"      {song_connector} SONG: {display_name} ({ext}){rating_str}\n")
                    f.write("\n")

    if not quiet:
        total_albums = sum(len(p) for p in genre_groups.values())
        print(f"\n{len(genre_groups)} wings ({total_albums} albums) written to: {outdir}")
    return 0


# =====================================
# Mode: FLAC integrity
# =====================================

def test_with_flac(filepath: str) -> Tuple[bool, str]:
    code, out, err = run_proc(["flac", "-t", "-s", str(filepath)])
    if code == 0:
        return True, ""
    return False, err or out or f"flac exited with code {code}"


def test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    code, out, err = run_proc(["ffmpeg", "-v", "error", "-nostats", "-i", str(filepath), "-f", "null", "-"])
    if code == 0 and not err:
        return True, ""
    if code == 0 and err:
        return False, err
    return False, err or out or f"ffmpeg exited with code {code}"


def test_flac(filepath: str, prefer: str) -> Tuple[bool, str, str]:
    have_flac = has_tool("flac")
    have_ffmpeg = has_tool("ffmpeg")

    # Build tool order based on preference
    tools: List[Tuple[str, Any]] = []
    if prefer == "ffmpeg":
        if have_ffmpeg:
            tools.append(("ffmpeg", test_with_ffmpeg))
        if have_flac:
            tools.append(("flac", test_with_flac))
    else:
        if have_flac:
            tools.append(("flac", test_with_flac))
        if have_ffmpeg:
            tools.append(("ffmpeg", test_with_ffmpeg))

    if not tools:
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."

    for name, func in tools:
        ok, msg = func(filepath)
        if ok:
            return True, name, ""
    # All tools failed — return the last result
    return False, name, msg


def run_flac_mode(root: str, output: str, workers: int, prefer: str, *, quiet: bool = False) -> int:
    root = os.path.abspath(root)
    flacs = _find_files_by_ext_path(Path(root), ".flac")
    total = len(flacs)

    if total == 0:
        if not quiet:
            print(f"No FLAC files found under: {root}")
        return 0

    if not (has_tool("flac") or has_tool("ffmpeg")):
        if not quiet:
            print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH.", file=sys.stderr)
        return 2

    if not quiet:
        print(f"Found {total} FLAC files under: {root}")

    errors: List[Tuple[str, str, str]] = []

    def worker(path: Path) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(str(path), prefer)
            return str(path), ok, method, msg
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return str(path), False, "exception", repr(e)

    pbar = _make_pbar(total, "Testing FLACs", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}
    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(worker, p): p for p in flacs}
        for fut in as_completed(futures):
            path, ok, method, msg = fut.result()
            if not ok:
                errors.append((path, method, msg))
            pbar.update(1)
    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Cancelling FLAC checks...")
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        pbar.close()

    if errors:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("FLAC INTEGRITY REPORT\n")
            f.write(f"Root: {root}\n")
            f.write(f"Scanned: {total}  Errors: {len(errors)}\n")
            f.write("=" * 60 + "\n\n")
            for i, (path, method, msg) in enumerate(errors, 1):
                rel = os.path.relpath(path, root)
                f.write(f"  {i:>3}. {rel}\n")
                f.write(f"       Tool: {method}\n")
                f.write(f"       Error: {msg}\n\n")
        if not quiet:
            print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}")
    elif not quiet:
        print("✅ All FLAC files passed integrity checks.")
    return 1 if errors else 0


# =====================================
# Mode: MP3 decode check
# =====================================

def _find_ffmpeg(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        p = Path(explicit_path)
        return str(p) if p.exists() else None
    return shutil.which("ffmpeg")


def _find_files_by_ext_path(root: Path, ext: str) -> List[Path]:
    """Walk tree and return all files matching extension as Path objects."""
    out: List[Path] = []
    root = root.expanduser().resolve()
    if root.is_file() and root.suffix.lower() == ext:
        return [root]
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if os.path.splitext(fn)[1].lower() == ext:
                out.append(Path(dirpath) / fn)
    return out


def _mutagen_header_info(path: Path) -> Dict[str, Any]:
    if not HAVE_MUTAGEN_MP3:
        return {}
    try:
        audio = MUTAGEN_MP3(path)
        info = getattr(audio, 'info', None)
        if not info:
            return {}
        return {
            "duration_s": round(getattr(info, "length", 0.0) or 0.0, 3),
            "bitrate_kbps": int((getattr(info, "bitrate", 0) or 0) / 1000),
            "sample_rate_hz": getattr(info, "sample_rate", None),
            "mode": getattr(info, "mode", None),
            "vbr_mode": getattr(info, "bitrate_mode", None).__class__.__name__
            if getattr(info, "bitrate_mode", None)
            else None,
        }
    except Exception:
        return {}


def _ffmpeg_decode_check(ffmpeg_path: Optional[str], path: Path) -> Tuple[bool, str]:
    if not ffmpeg_path:
        return True, "FFmpeg not available; skipped decode check (status=warn)"
    cmd = [ffmpeg_path, "-v", "error", "-nostats", "-hide_banner", "-i", str(path), "-f", "null", "-"]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return False, f"FFmpeg invocation failed: {e!r}"
    stderr = (proc.stderr or "").strip()
    if stderr:
        return False, stderr
    return True, "decode ok"


def _scan_one_file(path: Path, ffmpeg_path: Optional[str], *, enrich: bool = False) -> Dict[str, Any]:
    """Scan a single audio file for decode errors. If enrich=True, also pull
    mutagen header info (bitrate, duration, sample rate, VBR mode)."""
    row: Dict[str, Any] = {
        "path": str(path), "size_bytes": None, "status": "ok", "details": "",
    }
    if enrich:
        row.update({"duration_s": None, "bitrate_kbps": None,
                     "sample_rate_hz": None, "mode": None, "vbr_mode": None})

    try:
        row["size_bytes"] = path.stat().st_size
    except Exception as e:
        row["status"] = "error"
        row["details"] = f"stat failed: {e!r}"
        return row

    if enrich:
        row.update({k: v for k, v in _mutagen_header_info(path).items() if k in row})

    ok, msg = _ffmpeg_decode_check(ffmpeg_path, path)
    if "FFmpeg not available" in msg:
        row["status"] = "warn"
        row["details"] = msg
    elif not ok:
        row["status"] = "error"
        row["details"] = msg
    else:
        row["details"] = msg
    return row


def _format_row_meta(row: Dict[str, Any]) -> str:
    """Format metadata fields into a compact summary string."""
    parts: List[str] = []
    if row.get("bitrate_kbps"):
        parts.append(f"{row['bitrate_kbps']}kbps")
    if row.get("sample_rate_hz"):
        parts.append(f"{row['sample_rate_hz']}Hz")
    if row.get("duration_s"):
        parts.append(f"{row['duration_s']}s")
    if row.get("vbr_mode") and row["vbr_mode"] != "None":
        parts.append(row["vbr_mode"])
    return "  ".join(parts)


def _run_decode_scan(
        root: str, output: str, workers: int, ffmpeg: Optional[str],
        *, ext: str, report_title: str, default_output: str,
        ffmpeg_required: bool, enrich: bool,
        only_errors: bool, verbose: bool, quiet: bool,
) -> int:
    """Unified decode-check scanner for MP3, Opus, and future formats."""
    root_path = Path(os.path.abspath(root))
    ffmpeg_path = _find_ffmpeg(ffmpeg)

    if not ffmpeg_path:
        if ffmpeg_required:
            if not quiet:
                print(f"[warn] FFmpeg not found. Required for {ext.strip('.')} decode testing.",
                      file=sys.stderr)
            return 2
        elif not quiet:
            print("[warn] FFmpeg not found. Install it or pass --ffmpeg /path/to/ffmpeg",
                  file=sys.stderr)

    targets = _find_files_by_ext_path(root_path, ext)

    if not targets:
        if not quiet:
            print(f"No {ext} files found.", file=sys.stderr)
        return 0

    label = ext.strip('.').upper()
    started = time.time()
    oks = warns = errs = 0
    results: List[Dict[str, Any]] = []

    pbar = _make_pbar(len(targets), f"Scanning {label}", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

    if verbose:
        only_errors = False
        quiet = False

    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {
            ex.submit(_scan_one_file, p, ffmpeg_path, enrich=enrich): p
            for p in targets
        }

        for fut in as_completed(futures):
            row = fut.result()
            status = row.get("status")
            if status == "ok":
                oks += 1
            elif status == "warn":
                warns += 1
            else:
                errs += 1

            if not (only_errors and status == "ok"):
                results.append(row)

            pbar.update(1)

    except KeyboardInterrupt:
        if not quiet:
            print(f"\nInterrupted by user. Cancelling {label} scan…", file=sys.stderr)
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        pbar.close()

    elapsed = time.time() - started
    out_path = Path(output or default_output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"{report_title}\n")
        f.write(f"Root: {root_path}\n")
        f.write(f"Scanned: {len(targets)}  OK: {oks}  Warn: {warns}  Error: {errs}\n")
        f.write(f"Elapsed: {elapsed:.1f}s\n")
        f.write("=" * 60 + "\n\n")

        error_rows = [r for r in results if r["status"] == "error"]
        warn_rows = [r for r in results if r["status"] == "warn"]
        ok_rows = [r for r in results if r["status"] == "ok"]

        if error_rows:
            f.write(f"ERRORS ({len(error_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in error_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                f.write(f"  {rel}\n")
                if r.get("details"):
                    f.write(f"    {r['details']}\n")
                if enrich:
                    meta = _format_row_meta(r)
                    if meta:
                        f.write(f"    {meta}\n")
                f.write("\n")

        if warn_rows:
            f.write(f"WARNINGS ({len(warn_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in warn_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                f.write(f"  {rel}\n")
                if r.get("details"):
                    f.write(f"    {r['details']}\n")
                f.write("\n")

        if ok_rows:
            f.write(f"OK ({len(ok_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in ok_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                if enrich:
                    meta = _format_row_meta(r)
                    if meta:
                        f.write(f"  {rel}  [{meta}]\n")
                        continue
                f.write(f"  {rel}\n")

    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(f"ok: {oks}  warn: {warns}  error: {errs}")
        print(f"Report written to: {out_path}")
    return 1 if errs > 0 else 0


def run_mp3_mode(
        root: str, output: str, workers: int, ffmpeg: Optional[str],
        *, only_errors: bool, verbose: bool, quiet: bool,
) -> int:
    return _run_decode_scan(
        root, output, workers, ffmpeg,
        ext=".mp3", report_title="MP3 INTEGRITY REPORT",
        default_output=DEFAULT_MP3_OUTPUT, ffmpeg_required=False,
        enrich=True, only_errors=only_errors, verbose=verbose, quiet=quiet,
    )


def run_opus_mode(
        root: str, output: str, workers: int, ffmpeg: Optional[str],
        *, only_errors: bool, verbose: bool, quiet: bool,
) -> int:
    return _run_decode_scan(
        root, output, workers, ffmpeg,
        ext=".opus", report_title="OPUS INTEGRITY REPORT",
        default_output=DEFAULT_OPUS_OUTPUT, ffmpeg_required=True,
        enrich=False, only_errors=only_errors, verbose=verbose, quiet=quiet,
    )


# =====================================
# Mode: Extract cover art
# =====================================

def _extract_art_from_flac(filepath: str) -> Optional[bytes]:
    """Extract embedded art from a FLAC file."""
    try:
        audio = FLAC(filepath)
        pictures = audio.pictures
        if pictures:
            # Prefer front cover (type 3), fall back to first available
            for pic in pictures:
                if pic.type == 3:
                    return pic.data
            return pictures[0].data
    except Exception as e:
        print(f"  [!] Error reading FLAC art from {filepath}: {e}")
    return None


def _extract_art_from_opus(filepath: str) -> Optional[bytes]:
    """Extract embedded art from an Opus file (METADATA_BLOCK_PICTURE)."""
    try:
        audio = MutagenFile(filepath)
        if audio is None or audio.tags is None:
            return None
        b64_data = audio.tags.get("METADATA_BLOCK_PICTURE")
        if not b64_data:
            return None
        for b64_entry in b64_data:
            try:
                data = base64.b64decode(b64_entry)
                picture = Picture(data)
                return picture.data
            except Exception:
                continue
    except Exception as e:
        print(f"  [!] Error reading Opus art from {filepath}: {e}")
    return None


def _extract_art_from_mp3(filepath: str) -> Optional[bytes]:
    """Extract embedded art from an MP3 file (ID3 APIC frame)."""
    if not HAVE_MUTAGEN_MP3:
        return None
    try:
        audio = MUTAGEN_MP3(filepath)
        if audio.tags is None:
            return None
        # Prefer front cover (type 3), fall back to first APIC
        first_apic = None
        for tag in audio.tags.values():
            if getattr(tag, 'FrameID', None) == 'APIC':
                if first_apic is None:
                    first_apic = tag.data
                if getattr(tag, 'type', None) == 3:
                    return tag.data
        return first_apic
    except Exception as e:
        print(f"  [!] Error reading MP3 art from {filepath}: {e}")
    return None


def _extract_art_from_m4a(filepath: str) -> Optional[bytes]:
    """Extract embedded art from an M4A/MP4 file (covr atom)."""
    try:
        audio = MP4(filepath)
        if audio.tags is None:
            return None
        covr = audio.tags.get('covr')
        if covr and len(covr) > 0:
            return bytes(covr[0])
    except Exception as e:
        print(f"  [!] Error reading M4A art from {filepath}: {e}")
    return None


# Map extensions to their extraction functions
_ART_EXTRACTORS = {
    '.flac': _extract_art_from_flac,
    '.opus': _extract_art_from_opus,
    '.ogg': _extract_art_from_opus,  # OGG Vorbis uses same METADATA_BLOCK_PICTURE
    '.m4a': _extract_art_from_m4a,
    '.mp3': _extract_art_from_mp3,
}


def _extract_best_art(directory: str) -> Optional[bytes]:
    """
    Find the best embedded art in a directory by scanning files in format
    priority order: FLAC > Opus/OGG > M4A > MP3.
    Returns the first successful extraction or None.
    """
    try:
        dir_files = os.listdir(directory)
    except OSError:
        return None

    # Group files by extension
    files_by_ext: Dict[str, List[str]] = defaultdict(list)
    for f in dir_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in _ART_EXTRACTORS:
            files_by_ext[ext].append(f)

    # Try each format in priority order
    for ext in ART_FORMAT_PRIORITY:
        if ext not in files_by_ext:
            continue
        extractor = _ART_EXTRACTORS.get(ext)
        if not extractor:
            continue
        # Try only the first file of each format (they should all have the same art)
        filepath = os.path.join(directory, files_by_ext[ext][0])
        data = extractor(filepath)
        if data:
            return data

    return None


def _has_embedded_art(directory: str) -> bool:
    """Quick check: does any audio file in this directory have embedded art?"""
    return _extract_best_art(directory) is not None


def run_extract_art(root: str, *, quiet: bool = False, dry_run: bool = False) -> int:
    """Walk tree, extract cover art to cover.jpg for directories that lack it."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for art extraction.", file=sys.stderr)
        return 2

    root = os.path.abspath(root)
    extracted = 0
    skipped = 0
    failed = 0

    if not quiet:
        print(f"Scanning for missing cover art under: {root}")

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        # Only process directories that contain audio files
        has_audio = any(
            is_audio(f) for f in files
        )
        if not has_audio:
            continue

        # Case-insensitive check for existing cover
        if _has_cover_file(dirpath):
            skipped += 1
            continue

        if not quiet:
            print(f"[+] Processing: {dirpath}")

        image_data = _extract_best_art(dirpath)
        if image_data:
            output_path = os.path.join(dirpath, "cover.jpg")
            if dry_run:
                print(f"  -> [dry-run] Would extract art to {output_path}")
                extracted += 1
            else:
                try:
                    with open(output_path, "wb") as f:
                        f.write(image_data)
                    if not quiet:
                        print(f"  -> Extracted art to {output_path}")
                    extracted += 1
                except OSError as e:
                    print(f"  [!] Write failed: {e}")
                    failed += 1
        else:
            if not quiet:
                print("  [!] No embedded art found in any audio file.")
            failed += 1

    if not quiet:
        print(f"\nDone. Extracted: {extracted}  Skipped (art exists): {skipped}  No art found: {failed}")
    return 0


# =====================================
# Mode: Missing art report
# =====================================

def run_missing_art(root: str, output: str, *, quiet: bool = False) -> int:
    """Report directories that have audio files but no cover art (folder or embedded)."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for art detection.", file=sys.stderr)
        return 2

    root = os.path.abspath(root)
    missing: List[Dict[str, str]] = []

    if not quiet:
        print(f"Scanning for missing art under: {root}")

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        audio_files = [
            f for f in files if is_audio(f)
        ]
        if not audio_files:
            continue

        has_folder_art = _has_cover_file(dirpath)
        has_embedded = _has_embedded_art(dirpath) if not has_folder_art else True

        if not has_folder_art and not has_embedded:
            missing.append({
                "directory": dirpath,
                "audio_count": str(len(audio_files)),
                "has_folder_art": "no",
                "has_embedded_art": "no",
            })
        elif not has_folder_art:
            # Has embedded but no folder art — worth noting
            missing.append({
                "directory": dirpath,
                "audio_count": str(len(audio_files)),
                "has_folder_art": "no",
                "has_embedded_art": "yes",
            })

    out_path = os.path.abspath(output or DEFAULT_MISSING_ART_OUTPUT)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    no_art_at_all = [m for m in missing if m["has_embedded_art"] == "no"]
    embedded_only = [m for m in missing if m["has_embedded_art"] == "yes"]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("MISSING ART REPORT\n")
        f.write(f"Root: {root}\n")
        f.write(f"No art at all: {len(no_art_at_all)}  Embedded only: {len(embedded_only)}\n")
        f.write("=" * 60 + "\n\n")

        if no_art_at_all:
            f.write("NO ART (no folder image, no embedded art)\n")
            f.write("-" * 40 + "\n")
            for m in no_art_at_all:
                rel = os.path.relpath(m["directory"], root)
                f.write(f"  {rel}  ({m['audio_count']} files)\n")
            f.write("\n")

        if embedded_only:
            f.write("EMBEDDED ONLY (no folder image)\n")
            f.write("-" * 40 + "\n")
            for m in embedded_only:
                rel = os.path.relpath(m["directory"], root)
                f.write(f"  {rel}  ({m['audio_count']} files)\n")
            f.write("\n")

    if not quiet:
        print(f"\nResults written to: {out_path}")
        print(f"  No art at all: {len(no_art_at_all)}")
        print(f"  Embedded only (no folder art): {len(embedded_only)}")
    return 0


# =====================================
# Mode: Duplicate detection
# =====================================

def run_duplicates(root: str, output: str, *, quiet: bool = False) -> int:
    """Detect same artist+album appearing in multiple directories or formats."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for duplicate detection.", file=sys.stderr)
        return 2

    root = os.path.abspath(root)
    # Key: (normalized_artist, normalized_album) -> list of (directory, formats_found)
    album_map: Dict[Tuple[str, str], List[Tuple[str, set]]] = defaultdict(list)

    if not quiet:
        print(f"Scanning for duplicates under: {root}")

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        audio_files = [
            f for f in files if is_audio(f)
        ]
        if not audio_files:
            continue

        # Sample the first audio file for artist+album tags
        sample_path = os.path.join(dirpath, audio_files[0])
        t = get_all_tags(sample_path)
        artist = t.artist
        album = t.album

        if not artist or not album:
            # Try folder name heuristics: parent = artist, current = album
            # This is a reasonable fallback for well-organized libraries
            album = album or os.path.basename(dirpath)
            artist = artist or os.path.basename(os.path.dirname(dirpath))

        key = (artist.lower().strip(), album.lower().strip())
        formats = {os.path.splitext(f)[1].lower() for f in audio_files}
        album_map[key].append((dirpath, formats))

    # Filter to only entries that appear more than once
    duplicates = {k: v for k, v in album_map.items() if len(v) > 1}

    out_path = os.path.abspath(output or DEFAULT_DUPLICATES_OUTPUT)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    total_dupes = sum(len(v) for v in duplicates.values())

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("DUPLICATE ALBUM REPORT\n")
        f.write(f"Root: {root}\n")
        f.write(f"Duplicated albums: {len(duplicates)}  Total directories: {total_dupes}\n")
        f.write("=" * 60 + "\n\n")

        for i, ((artist, album), locations) in enumerate(sorted(duplicates.items()), 1):
            f.write(f"  {i}. {artist} — {album}\n")
            for directory, formats in locations:
                rel = os.path.relpath(directory, root)
                fmt_str = " ".join(sorted(formats))
                f.write(f"     └── {rel}  [{fmt_str}]\n")
            f.write("\n")

    if not quiet:
        print(f"\nFound {len(duplicates)} duplicated album(s) across {total_dupes} directories.")
        print(f"Results written to: {out_path}")
    return 0


# =====================================
# Mode: Tag audit
# =====================================

def run_tag_audit(root: str, output: str, *, quiet: bool = False) -> int:
    """Report audio files missing title, artist, track number, or genre."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for tag auditing.", file=sys.stderr)
        return 2

    root = os.path.abspath(root)
    issues: List[Dict[str, str]] = []

    if not quiet:
        print(f"Auditing tags under: {root}")

    total = count_audio_files(root)
    pbar = _make_pbar(total, "Auditing tags", quiet)

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            pbar.update(1)

            filepath = os.path.join(dirpath, f)
            t = get_all_tags(filepath)

            missing_fields: List[str] = []
            if not t.title:
                missing_fields.append("title")
            if not t.artist:
                missing_fields.append("artist")
            if t.trackno is None:
                missing_fields.append("tracknumber")
            if not t.genre:
                missing_fields.append("genre")

            if missing_fields:
                issues.append({
                    "path": filepath,
                    "format": ext.strip('.'),
                    "missing": ", ".join(missing_fields),
                })

    pbar.close()

    out_path = os.path.abspath(output or DEFAULT_TAG_AUDIT_OUTPUT)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Build breakdown counts
    field_counts: Counter = Counter()
    for issue in issues:
        for field in issue["missing"].split(", "):
            field_counts[field] += 1

    # Group issues by directory for readability
    by_dir: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for issue in issues:
        parent = os.path.dirname(issue["path"])
        by_dir[parent].append(issue)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("TAG AUDIT REPORT\n")
        f.write(f"Root: {root}\n")
        f.write(f"Scanned: {total}  Incomplete: {len(issues)}\n")
        if field_counts:
            breakdown = "  ".join(f"{field}: {count}" for field, count in field_counts.most_common())
            f.write(f"Breakdown: {breakdown}\n")
        f.write("=" * 60 + "\n\n")

        for directory in sorted(by_dir.keys()):
            rel_dir = os.path.relpath(directory, root)
            f.write(f"  {rel_dir}/\n")
            for issue in by_dir[directory]:
                filename = os.path.basename(issue["path"])
                f.write(f"    {filename}  [{issue['format']}]  missing: {issue['missing']}\n")
            f.write("\n")

    if not quiet:
        print(f"\nAudited {total} files. Found {len(issues)} with incomplete tags.")
        print(f"Results written to: {out_path}")
        if field_counts:
            print("  Breakdown:")
            for field, count in field_counts.most_common():
                print(f"    {field}: {count}")

    return 0


# =====================================
# Mode: Library statistics
# =====================================
DEFAULT_STATS_OUTPUT = "library_stats.txt"


def _format_size(size_bytes: int) -> str:
    """Format byte count into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def run_stats(root: str, output: Optional[str], *, quiet: bool = False) -> int:
    """Generate a library-wide statistics report."""
    root = os.path.abspath(root)

    total_files = count_audio_files(root)
    if total_files == 0:
        if not quiet:
            print(f"No audio files found under: {root}")
        return 0

    if not quiet:
        print(f"Scanning {total_files} files under: {root}")

    pbar = _make_pbar(total_files, "Gathering stats", quiet)

    # Accumulators
    format_counts: Counter = Counter()
    format_sizes: Counter = Counter()
    genre_counts: Counter = Counter()
    artist_counts: Counter = Counter()
    rating_counts: Dict[str, int] = {
        "★★★★★ (5)": 0, "★★★★☆ (4)": 0, "★★★☆☆ (3)": 0,
        "★★☆☆☆ (2)": 0, "★☆☆☆☆ (1)": 0, "unrated": 0,
    }
    total_size = 0
    total_duration = 0.0
    album_dirs: set = set()
    artist_dirs: set = set()
    bitrates: List[int] = []
    fully_tagged = 0  # has title + artist + track + genre

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            filepath = os.path.join(dirpath, f)
            format_counts[ext] += 1

            try:
                fsize = os.path.getsize(filepath)
                total_size += fsize
                format_sizes[ext] += fsize
            except OSError:
                fsize = 0

            t = get_all_tags(filepath)

            # Artist/album tracking from directory structure
            rel = os.path.relpath(dirpath, root)
            parts = rel.split(os.sep)
            if len(parts) >= 1:
                artist_dirs.add(parts[0])
            if len(parts) >= 2:
                album_dirs.add(rel)

            # Artist from tags (prefer tag, fall back to directory)
            artist_name = t.artist or (parts[0] if parts else None)
            if artist_name:
                artist_counts[artist_name] += 1

            if t.genre:
                genre_counts[t.genre] += 1

            if t.rating is not None:
                r = int(t.rating)
                if r >= 5:
                    rating_counts["★★★★★ (5)"] += 1
                elif r >= 4:
                    rating_counts["★★★★☆ (4)"] += 1
                elif r >= 3:
                    rating_counts["★★★☆☆ (3)"] += 1
                elif r >= 2:
                    rating_counts["★★☆☆☆ (2)"] += 1
                else:
                    rating_counts["★☆☆☆☆ (1)"] += 1
            else:
                rating_counts["unrated"] += 1

            # Duration and bitrate — now carried by TagBundle
            if t.duration_s:
                total_duration += t.duration_s
            if t.bitrate_kbps:
                bitrates.append(t.bitrate_kbps)

            # Fully tagged check
            has_all = all([t.title, t.artist, t.trackno is not None, t.genre])
            if has_all:
                fully_tagged += 1

            pbar.update(1)

    pbar.close()

    # Build report
    lines: List[str] = []
    lines.append("LIBRARY STATISTICS")
    lines.append(f"Root: {root}")
    lines.append("=" * 60)
    lines.append("")

    # Overview
    lines.append("OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"  Total files:    {total_files}")
    lines.append(f"  Total size:     {_format_size(total_size)}")
    if total_duration > 0:
        hours = int(total_duration // 3600)
        mins = int((total_duration % 3600) // 60)
        lines.append(f"  Total duration: {hours}h {mins}m")
    lines.append(f"  Artists:        {len(artist_dirs)}")
    lines.append(f"  Albums:         {len(album_dirs)}")
    pct_tagged = (fully_tagged / total_files * 100) if total_files else 0
    lines.append(f"  Fully tagged:   {fully_tagged}/{total_files} ({pct_tagged:.0f}%)")
    lines.append("")

    # Format breakdown
    lines.append("FORMAT BREAKDOWN")
    lines.append("-" * 40)
    for ext, count in format_counts.most_common():
        pct = count / total_files * 100
        size_str = _format_size(format_sizes[ext])
        lines.append(f"  {ext:<8} {count:>6} files  ({pct:>5.1f}%)  {size_str:>10}")
    lines.append("")

    # Bitrate summary
    if bitrates:
        lines.append("BITRATE")
        lines.append("-" * 40)
        avg_br = sum(bitrates) / len(bitrates)
        min_br = min(bitrates)
        max_br = max(bitrates)
        lines.append(f"  Average: {avg_br:.0f} kbps")
        lines.append(f"  Range:   {min_br}–{max_br} kbps")
        # Flag low-quality files
        low_quality = sum(1 for b in bitrates if b < 192)
        if low_quality:
            lines.append(f"  Below 192 kbps: {low_quality} files")
        lines.append("")

    # Rating distribution
    rated = total_files - rating_counts["unrated"]
    lines.append(f"RATINGS ({rated} rated, {rating_counts['unrated']} unrated)")
    lines.append("-" * 40)
    for label in ["★★★★★ (5)", "★★★★☆ (4)", "★★★☆☆ (3)", "★★☆☆☆ (2)", "★☆☆☆☆ (1)"]:
        count = rating_counts[label]
        if count > 0:
            bar_len = min(30, int(count / max(1, total_files) * 150))
            bar = "█" * bar_len
            lines.append(f"  {label}  {count:>5}  {bar}")
    lines.append("")

    # Genre distribution (top 15)
    if genre_counts:
        lines.append(f"GENRES (top 15 of {len(genre_counts)})")
        lines.append("-" * 40)
        for genre, count in genre_counts.most_common(15):
            pct = count / total_files * 100
            lines.append(f"  {genre:<30} {count:>5}  ({pct:.1f}%)")
        lines.append("")

    # Top artists (top 15)
    if artist_counts:
        lines.append(f"TOP ARTISTS (by track count, top 15 of {len(artist_counts)})")
        lines.append("-" * 40)
        for artist, count in artist_counts.most_common(15):
            lines.append(f"  {artist:<35} {count:>5} tracks")
        lines.append("")

    report = "\n".join(lines) + "\n"

    # Write to file if output specified, otherwise stdout
    if output:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        if not quiet:
            print(f"\nStatistics written to: {out_path}")
    else:
        print()
        print(report)

    return 0


# =====================================
# CLI wiring
# =====================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="getMusic.py",
        description="Music library toolkit: tree, integrity, art, duplicates, tag audit"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--library", action="store_true", help="Generate library tree")
    group.add_argument("--ai-library", dest="ai_library", action="store_true",
                        help="Generate token-efficient library for AI recommendations")
    group.add_argument("--all-wings", dest="all_wings", action="store_true",
                        help="Generate separate library files for each genre")
    group.add_argument("--testFLAC", action="store_true", help="Verify FLAC files")
    group.add_argument("--testMP3", action="store_true", help="Verify MP3 files")
    group.add_argument("--testOpus", action="store_true", help="Verify Opus files via FFmpeg decode")
    group.add_argument("--extractArt", action="store_true", help="Extract embedded cover art to folder")
    group.add_argument("--missingArt", action="store_true", help="Report directories missing cover art")
    group.add_argument("--duplicates", action="store_true", help="Detect duplicate artist+album across formats")
    group.add_argument("--auditTags", action="store_true", help="Report files with incomplete tags")
    group.add_argument("--stats", action="store_true", help="Library-wide statistics summary")

    p.add_argument("--root", default=".", help="Root directory (default: current)")
    p.add_argument("--output", default=None, help="Output path")
    p.add_argument("--workers", type=int, default=4, help="Parallel workers (integrity modes)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac", help="Preferred tool (FLAC mode)")
    p.add_argument("--quiet", action="store_true", help="Minimize output")
    p.add_argument("--genres", action="store_true", help="Include album genres in library tree")
    p.add_argument("--paths", action="store_true", help="Include absolute directory paths at the album level")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="Preview changes without writing (extractArt)")

    # MP3/Opus specific
    try:
        BooleanFlag = argparse.BooleanOptionalAction
    except AttributeError:
        BooleanFlag = None  # type: ignore

    if BooleanFlag:
        p.add_argument("--only-errors", dest="only_errors", action=BooleanFlag, default=True,
                        help="Write only errors/warns (MP3/Opus modes)")
    else:
        p.add_argument("--only-errors", dest="only_errors", action="store_true", default=True,
                        help="Write only errors/warns (MP3/Opus modes)")

    p.add_argument("--ffmpeg", default=None, help="Path to ffmpeg")
    p.add_argument("--verbose", action="store_true", help="Verbose output")
    return p


def _prompt_str(label: str, default: Optional[str]) -> str:
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    try:
        raw = input(f"  {label} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)
    return raw or (default or "")


def _prompt_path(label: str, default: str = ".") -> str:
    """Prompt for a filesystem path, expanding ~ and making absolute."""
    return os.path.abspath(os.path.expanduser(_prompt_str(label, default)))


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def _box_menu(title: str, sections: list, width: int = 44) -> None:
    """Fallback text menu for environments without curses."""
    iw = width - 4
    print(f"\n  ╔{'═' * (width - 2)}╗")
    print(f"  ║ {title:^{iw}} ║")
    print(f"  ╠{'═' * (width - 2)}╣")
    first = True
    for header, items in sections:
        if not first:
            print(f"  ╟{'─' * (width - 2)}╢")
        first = False
        if header:
            print(f"  ║  {header:<{iw - 1}} ║")
        for item in items:
            print(f"  ║    {item:<{iw - 3}} ║")
    print(f"  ╚{'═' * (width - 2)}╝")


def _pause() -> None:
    """Wait for user acknowledgement before redrawing."""
    if _USE_CURSES:
        _tui_pause()
        return
    try:
        input("\n  Press Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        pass


# =====================================
# Curses TUI
# =====================================

_CP_FRAME = 1
_CP_TITLE = 2
_CP_HEADER = 3
_CP_ITEM = 4
_CP_SELECTED = 5
_CP_HINT = 6


def _init_tui_colors() -> None:
    """Set up curses color pairs for the TUI menus."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)


_TUI_BOX_W = 46
_TUI_INNER = _TUI_BOX_W - 2  # chars between the two ║ borders


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    """Write to curses screen, silently ignoring out-of-bounds errors."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _tui_select(title: str, sections: list,
                hints: str = "\u2191\u2193 Navigate  \u23ce Select  q Quit") -> Optional[tuple]:
    """Full-screen arrow-key menu using curses.

    sections: list of (header_or_empty, [item_labels]).
    Returns (section_idx, item_idx) on Enter, None on q/Esc.
    """
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    # Flatten into a selectable-item list
    flat: list[tuple[int, int]] = []
    for si, (_, items) in enumerate(sections):
        for ii in range(len(items)):
            flat.append((si, ii))

    def _draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        # Calculate total box height for vertical centering
        box_h = 3  # top border + title + mid border
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            box_h += len(items)
        box_h += 1  # bottom border

        y = max(0, (h - box_h - 2) // 2)

        # Top border
        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        # Title (bold white, centered)
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, f" {title:^{INNER - 2}} ",
              curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        # Title/body separator
        _safe_addstr(stdscr, y, bx, "\u2560" + "\u2550" * INNER + "\u2563", fa)
        y += 1

        idx = 0
        for si, (hdr, items) in enumerate(sections):
            # Section separator (except first)
            if si > 0:
                _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
                y += 1

            # Section header (bold yellow)
            if hdr:
                content = f"  {hdr}" + " " * (INNER - len(hdr) - 2)
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, content,
                      curses.color_pair(_CP_HEADER) | curses.A_BOLD)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1

            # Selectable items
            for ii, label in enumerate(items):
                is_sel = idx == cur
                if is_sel:
                    text = f" \u25ba {label}"
                    attr = curses.color_pair(_CP_SELECTED) | curses.A_BOLD
                else:
                    text = f"   {label}"
                    attr = curses.color_pair(_CP_ITEM)
                padded = text + " " * max(0, INNER - len(text))
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, padded[:INNER], attr)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
                idx += 1

        # Bottom border
        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        y += 2

        # Hint bar (centered, dim)
        hx = max(0, (w - len(hints)) // 2)
        _safe_addstr(stdscr, y, hx, hints,
              curses.color_pair(_CP_HINT) | curses.A_DIM)

        stdscr.refresh()

    def _run(stdscr) -> Optional[tuple]:
        _init_tui_colors()
        curses.curs_set(0)
        cur = 0
        while True:
            _draw(stdscr, cur)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                cur = (cur - 1) % len(flat)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur = (cur + 1) % len(flat)
            elif key in (curses.KEY_ENTER, 10, 13):
                return flat[cur]
            elif key in (ord('q'), ord('Q'), 27):
                return None
            elif key == curses.KEY_RESIZE:
                pass  # redraws on next loop iteration

    try:
        return curses.wrapper(_run)
    except curses.error:
        return None


def _tui_prompt_str(label: str, default: Optional[str]) -> str:
    """Curses-based single-line text prompt styled like the TUI menus."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str:
        _init_tui_colors()
        curses.curs_set(1)
        buf = list(default or "")

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            bx = max(0, (w - BOX_W) // 2)
            fa = curses.color_pair(_CP_FRAME)

            y = max(0, (h - 8) // 2)

            # Top border
            _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
            y += 1

            # Label row
            lbl = f"  {label}"
            padded_lbl = lbl + " " * max(0, INNER - len(lbl))
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, padded_lbl[:INNER],
                         curses.color_pair(_CP_HEADER) | curses.A_BOLD)
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1

            # Separator
            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1

            # Input field
            display = "".join(buf)
            max_input = INNER - 4
            if len(display) > max_input:
                visible = "\u2026" + display[-(max_input - 1):]
            else:
                visible = display
            input_text = f" > {visible}" + " " * max(0, INNER - len(visible) - 3)
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, input_text[:INNER],
                         curses.color_pair(_CP_ITEM))
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            input_y = y
            y += 1

            # Bottom border
            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2

            # Hints
            hints = "\u23ce Confirm  Esc Default"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(stdscr, y, hx, hints,
                         curses.color_pair(_CP_HINT) | curses.A_DIM)

            # Position cursor
            cursor_x = bx + 4 + min(len(display), max_input)
            try:
                stdscr.move(input_y, min(cursor_x, bx + BOX_W - 2))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                result = "".join(buf).strip()
                return result if result else (default or "")
            elif key == 27:  # Escape — use default
                return default or ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif key == curses.KEY_RESIZE:
                pass
            elif 32 <= key <= 126:
                buf.append(chr(key))

    try:
        return curses.wrapper(_run)
    except curses.error:
        # Fall back to plain input
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(130)
        return raw or (default or "")


def _tui_pause() -> None:
    """Curses-based 'press Enter to continue' styled like the TUI menus."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> None:
        _init_tui_colors()
        curses.curs_set(0)

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        y = max(0, (h - 5) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        msg = "Press Enter to continue\u2026"
        padded = f" {msg:^{INNER - 2}} "
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, padded[:INNER],
                     curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        stdscr.refresh()

        while True:
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13, ord('q'), ord('Q'), 27):
                return

    try:
        curses.wrapper(_run)
    except curses.error:
        try:
            input("\n  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            pass


# =====================================
# Fallback (non-curses) menu input
# =====================================

_MAIN_FALLBACK_MAP: Dict[str, Optional[tuple]] = {
    "1": (0, 0), "l": (0, 0), "lib": (0, 0), "library": (0, 0),
    "2": (0, 1), "stats": (0, 1),
    "3": (1, 0), "flac": (1, 0),
    "4": (1, 1), "mp3": (1, 1),
    "5": (1, 2), "opus": (1, 2),
    "6": (2, 0), "art": (2, 0), "extract": (2, 0),
    "7": (2, 1), "missing": (2, 1),
    "8": (3, 0), "dup": (3, 0), "dupes": (3, 0),
    "9": (3, 1), "audit": (3, 1), "tags": (3, 1),
    "q": None, "quit": None, "exit": None,
}

_LIB_FALLBACK_MAP: Dict[str, Optional[tuple]] = {
    "1": (0, 0), "tree": (0, 0), "lib": (0, 0),
    "2": (0, 1), "ai": (0, 1),
    "3": (0, 2), "wings": (0, 2),
    "b": None, "back": None, "": None,
}


def _fallback_input(prompt: str, mapping: dict) -> Any:
    """Text-input fallback when curses is unavailable."""
    try:
        ch = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return mapping.get(ch, "invalid")


# =====================================
# Menu entry points
# =====================================

_MAIN_SECTIONS = [
    ("LIBRARY", [
        "Library tree & exports                  \u2192",
        "Library statistics",
    ]),
    ("INTEGRITY", [
        "Test FLAC files",
        "Test MP3 files",
        "Test Opus files",
    ]),
    ("ARTWORK", [
        "Extract cover art",
        "Report missing art",
    ]),
    ("METADATA", [
        "Find duplicate albums",
        "Audit tags",
    ]),
    ("", ["Quit"]),
]

_LIB_SECTIONS = [
    ("", [
        "Build music library tree",
        "AI-readable library export",
        "Generate all wings (per-genre)",
    ]),
    ("", ["Back to main menu"]),
]

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()


def _select_main() -> Optional[tuple]:
    """Get a main-menu selection via curses or fallback."""
    if _USE_CURSES:
        return _tui_select(f"getMusic v{VERSION}", _MAIN_SECTIONS)
    _box_menu(f"getMusic v{VERSION}", [
        ("LIBRARY", ["1) Library tree & exports          \u2192",
                      "2) Library statistics"]),
        ("INTEGRITY", ["3) Test FLAC files", "4) Test MP3 files",
                        "5) Test Opus files"]),
        ("ARTWORK", ["6) Extract cover art", "7) Report missing art"]),
        ("METADATA", ["8) Find duplicate albums", "9) Audit tags"]),
        ("", ["q) Quit"]),
    ])
    return _fallback_input("  Select [1-9/q]: ", _MAIN_FALLBACK_MAP)


def _select_library() -> Optional[tuple]:
    """Get a library-submenu selection via curses or fallback."""
    if _USE_CURSES:
        return _tui_select("Library Tree & Exports", _LIB_SECTIONS,
                           hints="\u2191\u2193 Navigate  \u23ce Select  Esc Back")
    _box_menu("Library Tree & Exports", [
        ("", ["1) Build music library tree",
              "2) AI-readable library export",
              "3) Generate all wings (per-genre)"]),
        ("", ["b) Back to main menu"]),
    ])
    return _fallback_input("  Select [1-3/b]: ", _LIB_FALLBACK_MAP)


def _library_submenu() -> None:
    """Library tree & exports submenu."""
    while True:
        result = _select_library()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (1, 0):  # Back / Esc
            return

        _reset_terminal()

        if result == (0, 0):
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT) or DEFAULT_LIBRARY_OUTPUT
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith('y')
            write_music_library_tree(root, output, quiet=False, show_genre=show_g)
            print(f"\n  Library written to {output}")
            _pause()

        elif result == (0, 1):
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_AI_LIBRARY_OUTPUT) or DEFAULT_AI_LIBRARY_OUTPUT
            write_ai_library(root, output, quiet=False)
            _pause()

        elif result == (0, 2):
            root = _prompt_path("Root directory")
            outdir = _prompt_str("Output directory", "wings") or "wings"
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith('y')
            show_p = _prompt_str("Include paths? (y/N)", "N").lower().startswith('y')
            write_all_wings(root, outdir, quiet=False, show_genre=show_g, show_paths=show_p)
            _pause()


def interactive_menu() -> int:
    while True:
        _reset_terminal()
        result = _select_main()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (4, 0):  # Quit
            return 0

        if result == (0, 0):  # Library submenu
            _library_submenu()

        elif result == (0, 1):  # Stats
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file (leave blank for screen)", "").strip() or None
            run_stats(root, output, quiet=False)
            _pause()

        elif result == (1, 0):  # FLAC
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_FLAC_OUTPUT) or DEFAULT_FLAC_OUTPUT
            workers = _prompt_int("Workers", 4)
            pref = _prompt_str("Preferred tool (flac/ffmpeg)", "flac").lower()
            run_flac_mode(root, output, workers, pref, quiet=False)
            _pause()

        elif result == (1, 1):  # MP3
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_MP3_OUTPUT) or DEFAULT_MP3_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_mp3_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )
            _pause()

        elif result == (1, 2):  # Opus
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_OPUS_OUTPUT) or DEFAULT_OPUS_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_opus_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )
            _pause()

        elif result == (2, 0):  # Extract art
            root = _prompt_path("Root directory")
            dry = _prompt_str("Dry run? (y/N)", "N").lower().startswith('y')
            run_extract_art(root, quiet=False, dry_run=dry)
            _pause()

        elif result == (2, 1):  # Missing art
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_MISSING_ART_OUTPUT) or DEFAULT_MISSING_ART_OUTPUT
            run_missing_art(root, output, quiet=False)
            _pause()

        elif result == (3, 0):  # Duplicates
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_DUPLICATES_OUTPUT) or DEFAULT_DUPLICATES_OUTPUT
            run_duplicates(root, output, quiet=False)
            _pause()

        elif result == (3, 1):  # Audit tags
            root = _prompt_path("Root directory")
            output = _prompt_str("Output file", DEFAULT_TAG_AUDIT_OUTPUT) or DEFAULT_TAG_AUDIT_OUTPUT
            run_tag_audit(root, output, quiet=False)
            _pause()


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        args = build_parser().parse_args(argv)
        root = os.path.abspath(os.path.expanduser(args.root))

        if args.library:
            output = args.output or DEFAULT_LIBRARY_OUTPUT
            write_music_library_tree(root, output, quiet=args.quiet, show_genre=args.genres)
            return 0

        if args.ai_library:
            output = args.output or DEFAULT_AI_LIBRARY_OUTPUT
            write_ai_library(root, output, quiet=args.quiet)
            return 0

        if args.all_wings:
            outdir = args.output or "wings"
            return write_all_wings(root, outdir, quiet=args.quiet, show_genre=args.genres, show_paths=args.paths)

        if args.testFLAC:
            output = args.output or DEFAULT_FLAC_OUTPUT
            return run_flac_mode(root, output, args.workers, args.prefer, quiet=args.quiet)

        if args.testMP3:
            output = args.output or DEFAULT_MP3_OUTPUT
            return run_mp3_mode(
                root, output, args.workers, args.ffmpeg,
                only_errors=args.only_errors, verbose=args.verbose, quiet=args.quiet,
            )

        if args.testOpus:
            output = args.output or DEFAULT_OPUS_OUTPUT
            return run_opus_mode(
                root, output, args.workers, args.ffmpeg,
                only_errors=args.only_errors, verbose=args.verbose, quiet=args.quiet,
            )

        if args.extractArt:
            return run_extract_art(root, quiet=args.quiet, dry_run=args.dry_run)

        if args.missingArt:
            output = args.output or DEFAULT_MISSING_ART_OUTPUT
            return run_missing_art(root, output, quiet=args.quiet)

        if args.duplicates:
            output = args.output or DEFAULT_DUPLICATES_OUTPUT
            return run_duplicates(root, output, quiet=args.quiet)

        if args.auditTags:
            output = args.output or DEFAULT_TAG_AUDIT_OUTPUT
            return run_tag_audit(root, output, quiet=args.quiet)

        if args.stats:
            return run_stats(root, args.output, quiet=args.quiet)

        build_parser().print_help()
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _reset_terminal()

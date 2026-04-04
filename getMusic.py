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

Usage examples:
  python getMusic.py --library --root ~/Music --output library.txt --genres
  python getMusic.py --testFLAC --root ~/Music --output flac_errors.txt --workers 4
  python getMusic.py --testMP3 --root ~/Music --output mp3_errors.txt --workers 4
  python getMusic.py --testOpus --root ~/Music --output opus_errors.txt --workers 4
  python getMusic.py --extractArt --root ~/Music
  python getMusic.py --missingArt --root ~/Music --output missing_art.txt
  python getMusic.py --duplicates --root ~/Music --output duplicates.txt
  python getMusic.py --auditTags --root ~/Music --output tag_audit.txt

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


class TagBundle(NamedTuple):
    """All metadata we care about, extracted once per file."""
    title: Optional[str] = None
    artist: Optional[str] = None
    trackno: Optional[int] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[float] = None

# --- Mutagen imports ---
HAVE_MUTAGEN_BASE = False
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3, ID3NoHeaderError
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
VERSION = "2.1.0"

DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.txt"
DEFAULT_MP3_OUTPUT = "mp3_scan_results.txt"
DEFAULT_OPUS_OUTPUT = "opus_scan_results.txt"
DEFAULT_MISSING_ART_OUTPUT = "missing_art.txt"
DEFAULT_DUPLICATES_OUTPUT = "duplicates.txt"
DEFAULT_TAG_AUDIT_OUTPUT = "tag_audit.txt"

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.wav', '.wma', '.aac'}

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
        total += sum(1 for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS)
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


def _get_cover_file_path(directory: str) -> Optional[str]:
    """Returns the path to an existing cover file if one exists (case-insensitive)."""
    try:
        for f in os.listdir(directory):
            if f.lower() in COVER_NAMES:
                return os.path.join(directory, f)
    except OSError:
        pass
    return None


def _make_pbar(total: int, desc: str, quiet: bool):
    """Create a progress bar — tqdm if available, else None."""
    if HAVE_TQDM and not quiet:
        return tqdm(total=total, unit="file", desc=desc, dynamic_ncols=True)
    return None


# =====================================
# Tag helpers
# =====================================

def get_title_artist_track(file_path: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    title = artist = None
    trackno: Optional[int] = None

    if not HAVE_MUTAGEN_BASE:
        return None, None, None

    try:
        # 1. Try easy abstraction
        try:
            easy = MutagenFile(file_path, easy=True)
            if easy and easy.tags:
                title = _first_text(easy.tags.get('title'))
                artist = _first_text(easy.tags.get('artist')) or _first_text(easy.tags.get('albumartist'))
                trackno = _parse_track_number(easy.tags.get('tracknumber'))
        except Exception:
            pass

        # 2. Fallback to format-specific parsing
        if not (title and artist and trackno):
            audio = MutagenFile(file_path)
            if not audio:
                return title, artist, trackno

            ext = os.path.splitext(file_path)[1].lower()

            if ext == '.mp3':
                try:
                    id3 = ID3(file_path)
                    if title is None and id3.get('TIT2'):
                        title = _first_text(id3.get('TIT2').text)
                    if artist is None:
                        if id3.get('TPE1'):
                            artist = _first_text(id3.get('TPE1').text)
                        elif id3.get('TPE2'):
                            artist = _first_text(id3.get('TPE2').text)
                    if trackno is None and id3.get('TRCK'):
                        trackno = _parse_track_number(id3.get('TRCK').text)
                except ID3NoHeaderError:
                    pass

            elif isinstance(audio, MP4):
                tags = getattr(audio, 'tags', {}) or {}
                if title is None:
                    title = _first_text(tags.get('\xa9nam'))
                if artist is None:
                    artist = _first_text(tags.get('\xa9ART')) or _first_text(tags.get('aART'))
                if trackno is None:
                    trackno = _parse_track_number(tags.get('trkn'))

            elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
                tags = getattr(audio, 'tags', {}) or {}
                keys = {k.lower(): k for k in tags.keys()}
                if title is None and 'title' in keys:
                    title = _first_text(tags[keys['title']])
                if artist is None:
                    if 'artist' in keys:
                        artist = _first_text(tags[keys['artist']])
                    elif 'albumartist' in keys:
                        artist = _first_text(tags[keys['albumartist']])
                if trackno is None and 'tracknumber' in keys:
                    trackno = _parse_track_number(tags[keys['tracknumber']])

            elif isinstance(audio, ASF):
                tags = getattr(audio, 'tags', {}) or {}
                name_map = {k.lower(): k for k in tags.keys()}
                if title is None and (k := name_map.get('title')):
                    title = _first_text(tags.get(k))
                if artist is None and (k := name_map.get('author') or name_map.get('wm/albumartist')):
                    artist = _first_text(tags.get(k))
                if trackno is None and (k := name_map.get('wm/tracknumber') or name_map.get('tracknumber')):
                    trackno = _parse_track_number(tags.get(k))

    except Exception:
        pass

    return title, artist, trackno


def get_album(file_path: str) -> Optional[str]:
    """Extract album name from audio file tags."""
    if not HAVE_MUTAGEN_BASE:
        return None
    try:
        easy = MutagenFile(file_path, easy=True)
        if easy and easy.tags and 'album' in easy.tags:
            return _first_text(easy.tags['album'])

        audio = MutagenFile(file_path)
        if not audio:
            return None
        tags = getattr(audio, 'tags', {}) or {}

        if isinstance(audio, MP4):
            return _first_text(tags.get('\xa9alb'))

        # Vorbis-style or ID3 — try case-insensitive
        for k, v in tags.items():
            if str(k).lower() == 'album':
                return _first_text(v)
        # ID3 TALB
        if hasattr(tags, 'getall'):
            talb = tags.getall('TALB')
            if talb:
                return _first_text(talb[0])
    except Exception:
        pass
    return None


def get_genre(file_path: str) -> Optional[str]:
    """Extracts the genre from the audio file tags."""
    if not HAVE_MUTAGEN_BASE:
        return None
    try:
        try:
            easy = MutagenFile(file_path, easy=True)
            if easy and easy.tags and 'genre' in easy.tags:
                return _first_text(easy.tags['genre'])
        except Exception:
            pass

        audio = MutagenFile(file_path)
        if not audio:
            return None

        tags = getattr(audio, 'tags', {}) or {}

        if hasattr(tags, 'getall'):
            tcon = tags.getall('TCON')
            if tcon:
                return _first_text(tcon[0])

        for k, v in tags.items():
            k_lower = str(k).lower()
            if k_lower == 'genre':
                return _first_text(v)
            if k_lower == '\xa9gen':
                return _first_text(v)
            if k_lower == 'gnre':
                return _first_text(v)
            if k_lower == 'wm/genre':
                return _first_text(v)

    except Exception:
        pass
    return None


def get_rating(file_path: str) -> Optional[float]:
    if not HAVE_MUTAGEN_BASE:
        return None
    try:
        ext = os.path.splitext(file_path)[1].lower()
        audio = MutagenFile(file_path)
        if not audio:
            return None

        if ext == '.mp3':
            try:
                id3 = ID3(file_path)
            except ID3NoHeaderError:
                return None
            for popm in id3.getall('POPM'):
                if getattr(popm, 'email', '') == 'Windows Media Player 9 Series':
                    wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                    return wmp_map.get(popm.rating, normalize_rating(popm.rating))
            for popm in id3.getall('POPM'):
                if popm.rating > 0:
                    return normalize_rating(popm.rating)
            for txxx in id3.getall('TXXX'):
                desc = (txxx.desc or "").lower()
                if 'rating' in desc or desc in ('rate', 'score', 'stars'):
                    val = txxx.text[0] if txxx.text else None
                    if val and str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower() or 'score' in key.lower() or 'stars' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        elif isinstance(audio, MP4):
            for key, val in (audio.tags or {}).items():
                k = key.lower() if isinstance(key, str) else str(key).lower()
                if 'rate' in k or 'rating' in k:
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        elif isinstance(audio, ASF):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)
        return None
    except Exception:
        return None


def get_all_tags(file_path: str) -> TagBundle:
    """Extract all metadata in a single file open.  Replaces the pattern of
    calling get_title_artist_track + get_album + get_genre + get_rating
    independently (4 MutagenFile opens -> 1)."""
    if not HAVE_MUTAGEN_BASE:
        return TagBundle()

    title = artist = album = genre = None
    trackno: Optional[int] = None
    rating: Optional[float] = None

    try:
        # --- easy abstraction pass ---
        try:
            easy = MutagenFile(file_path, easy=True)
            if easy and easy.tags:
                title = _first_text(easy.tags.get('title'))
                artist = (_first_text(easy.tags.get('artist'))
                          or _first_text(easy.tags.get('albumartist')))
                trackno = _parse_track_number(easy.tags.get('tracknumber'))
                album = _first_text(easy.tags.get('album'))
                genre = _first_text(easy.tags.get('genre'))
        except Exception:
            pass

        # --- format-specific fallback (single open) ---
        audio = MutagenFile(file_path)
        if not audio:
            return TagBundle(title, artist, trackno, album, genre, rating)

        ext = os.path.splitext(file_path)[1].lower()
        tags = getattr(audio, 'tags', {}) or {}

        if ext == '.mp3':
            try:
                id3 = ID3(file_path)
                if title is None and id3.get('TIT2'):
                    title = _first_text(id3.get('TIT2').text)
                if artist is None:
                    if id3.get('TPE1'):
                        artist = _first_text(id3.get('TPE1').text)
                    elif id3.get('TPE2'):
                        artist = _first_text(id3.get('TPE2').text)
                if trackno is None and id3.get('TRCK'):
                    trackno = _parse_track_number(id3.get('TRCK').text)
                if album is None and id3.get('TALB'):
                    album = _first_text(id3.get('TALB').text)
                if genre is None:
                    tcon = id3.getall('TCON')
                    if tcon:
                        genre = _first_text(tcon[0])
                # Rating: POPM / TXXX
                for popm in id3.getall('POPM'):
                    if getattr(popm, 'email', '') == 'Windows Media Player 9 Series':
                        wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                        rating = wmp_map.get(popm.rating, normalize_rating(popm.rating))
                        break
                if rating is None:
                    for popm in id3.getall('POPM'):
                        if popm.rating > 0:
                            rating = normalize_rating(popm.rating)
                            break
                if rating is None:
                    for txxx in id3.getall('TXXX'):
                        desc = (txxx.desc or "").lower()
                        if 'rating' in desc or desc in ('rate', 'score', 'stars'):
                            val = txxx.text[0] if txxx.text else None
                            if val and str(val).replace('.', '').isdigit():
                                rating = normalize_rating(val)
                                break
            except ID3NoHeaderError:
                pass

        elif isinstance(audio, MP4):
            if title is None:
                title = _first_text(tags.get('\xa9nam'))
            if artist is None:
                artist = _first_text(tags.get('\xa9ART')) or _first_text(tags.get('aART'))
            if trackno is None:
                trackno = _parse_track_number(tags.get('trkn'))
            if album is None:
                album = _first_text(tags.get('\xa9alb'))
            if genre is None:
                for k in ('\xa9gen', 'gnre'):
                    v = tags.get(k)
                    if v:
                        genre = _first_text(v)
                        break
            for k, v in tags.items():
                kl = k.lower() if isinstance(k, str) else str(k).lower()
                if 'rate' in kl or 'rating' in kl:
                    v = v[0] if isinstance(v, list) else v
                    if str(v).replace('.', '').isdigit():
                        rating = normalize_rating(v)
                        break

        elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            keys = {k.lower(): k for k in tags.keys()}
            if title is None and 'title' in keys:
                title = _first_text(tags[keys['title']])
            if artist is None:
                if 'artist' in keys:
                    artist = _first_text(tags[keys['artist']])
                elif 'albumartist' in keys:
                    artist = _first_text(tags[keys['albumartist']])
            if trackno is None and 'tracknumber' in keys:
                trackno = _parse_track_number(tags[keys['tracknumber']])
            if album is None and 'album' in keys:
                album = _first_text(tags[keys['album']])
            if genre is None and 'genre' in keys:
                genre = _first_text(tags[keys['genre']])
            for key, val in tags.items():
                if 'rating' in key.lower() or 'score' in key.lower() or 'stars' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        rating = normalize_rating(val)
                        break

        elif isinstance(audio, ASF):
            name_map = {k.lower(): k for k in tags.keys()}
            if title is None and (k := name_map.get('title')):
                title = _first_text(tags.get(k))
            if artist is None and (k := name_map.get('author') or name_map.get('wm/albumartist')):
                artist = _first_text(tags.get(k))
            if trackno is None and (k := name_map.get('wm/tracknumber') or name_map.get('tracknumber')):
                trackno = _parse_track_number(tags.get(k))
            if album is None and (k := name_map.get('wm/albumtitle')):
                album = _first_text(tags.get(k))
            if genre is None and (k := name_map.get('wm/genre')):
                genre = _first_text(tags.get(k))
            for key, val in tags.items():
                if 'rating' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
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

    return TagBundle(title, artist, trackno, album, genre, rating)


# =====================================
# Mode: Library tree
# =====================================

def write_music_library_tree(root_dir: str, output_file: str, *, quiet: bool = False, show_genre: bool = False) -> None:
    root_dir = os.path.abspath(root_dir)
    total_files = count_audio_files(root_dir)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {root_dir}\n")

    current_file = 0
    pbar = _make_pbar(total_files, "Scanning library", quiet)

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
                        if os.path.splitext(s)[1].lower() in AUDIO_EXTENSIONS
                    ])

                    if not songs:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        f.write("      └── [No Audio Files Found]\n")
                        continue

                    # Get genre from first song if requested (uses unified reader)
                    genre_str = ""
                    if show_genre:
                        first_tags = get_all_tags(os.path.join(album_path, songs[0]))
                        if first_tags.genre:
                            genre_str = f" ({first_tags.genre})"

                    f.write(f"  {connector} ALBUM: {album}{genre_str}\n")

                    for j, song in enumerate(songs):
                        current_file += 1
                        if pbar:
                            pbar.update(1)
                        else:
                            update_progress(current_file, total_files, "Scanning")

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
    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Library scan cancelled.")
        return
    finally:
        if pbar:
            pbar.close()


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
    if prefer == "ffmpeg":
        if has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            if ok or not has_tool("flac"):
                return ok, "ffmpeg", ("" if ok else msg)
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", ("" if ok else msg)
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."
    else:
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", ("" if ok else msg)
        if has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            return ok, "ffmpeg", ("" if ok else msg)
        return False, "none", "Neither 'flac' nor 'ffmpeg' found in PATH."


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

    checked = 0
    pbar = _make_pbar(total, "Testing FLACs", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}
    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(worker, p): p for p in flacs}
        for fut in as_completed(futures):
            path, ok, method, msg = fut.result()
            checked += 1
            if not ok:
                errors.append((path, method, msg))
            if pbar:
                pbar.update(1)
            else:
                update_progress(checked, total, prefix="Testing FLACs")
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
        if pbar:
            pbar.close()

    if errors:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"FLAC INTEGRITY REPORT\n")
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
            if fn.lower().endswith(ext):
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


def _scan_one_mp3(path: Path, ffmpeg_path: Optional[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "path": str(path), "size_bytes": None, "status": "ok", "details": "",
        "duration_s": None, "bitrate_kbps": None, "sample_rate_hz": None,
        "mode": None, "vbr_mode": None,
    }
    try:
        row["size_bytes"] = path.stat().st_size
    except Exception as e:
        row["status"] = "error"
        row["details"] = f"stat failed: {e!r}"
        return row

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


def run_mp3_mode(
        root: str, output: str, workers: int, ffmpeg: Optional[str],
        *, only_errors: bool, verbose: bool, quiet: bool,
) -> int:
    root_path = Path(os.path.abspath(root))
    ffmpeg_path = _find_ffmpeg(ffmpeg)

    if not ffmpeg_path and not quiet:
        print("[warn] FFmpeg not found. Install it or pass --ffmpeg /path/to/ffmpeg", file=sys.stderr)

    targets = _find_files_by_ext_path(root_path, ".mp3")

    if not targets:
        if not quiet:
            print("No .mp3 files found.", file=sys.stderr)
        return 0

    started = time.time()
    oks = warns = errs = 0
    results: List[Dict[str, Any]] = []

    pbar = _make_pbar(len(targets), "Scanning MP3s", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

    if verbose:
        only_errors = False
        quiet = False

    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(_scan_one_mp3, p, ffmpeg_path): p for p in targets}

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

            if pbar:
                pbar.update(1)

    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Cancelling MP3 scan…", file=sys.stderr)
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        if pbar:
            pbar.close()

    elapsed = time.time() - started
    out_path = Path(output or DEFAULT_MP3_OUTPUT).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("MP3 INTEGRITY REPORT\n")
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
                meta = _format_mp3_meta(r)
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
                meta = _format_mp3_meta(r)
                if meta:
                    f.write(f"  {rel}  [{meta}]\n")
                else:
                    f.write(f"  {rel}\n")

    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(f"ok: {oks}  warn: {warns}  error: {errs}")
        print(f"Report written to: {out_path}")
    return 1 if errs > 0 else 0


def _format_mp3_meta(row: Dict[str, Any]) -> str:
    """Format MP3 metadata fields into a compact summary string."""
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


# =====================================
# Mode: Opus decode check (NEW)
# =====================================

def _scan_one_opus(path: Path, ffmpeg_path: Optional[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "path": str(path), "size_bytes": None, "status": "ok", "details": "",
    }
    try:
        row["size_bytes"] = path.stat().st_size
    except Exception as e:
        row["status"] = "error"
        row["details"] = f"stat failed: {e!r}"
        return row

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


def run_opus_mode(
        root: str, output: str, workers: int, ffmpeg: Optional[str],
        *, only_errors: bool, verbose: bool, quiet: bool,
) -> int:
    root_path = Path(os.path.abspath(root))
    ffmpeg_path = _find_ffmpeg(ffmpeg)
    if not ffmpeg_path:
        if not quiet:
            print("[warn] FFmpeg not found. Required for Opus decode testing.", file=sys.stderr)
        return 2

    targets = _find_files_by_ext_path(root_path, ".opus")

    if not targets:
        if not quiet:
            print("No .opus files found.", file=sys.stderr)
        return 0

    started = time.time()
    oks = warns = errs = 0
    results: List[Dict[str, Any]] = []

    pbar = _make_pbar(len(targets), "Scanning Opus", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

    if verbose:
        only_errors = False
        quiet = False

    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(_scan_one_opus, p, ffmpeg_path): p for p in targets}

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

            if pbar:
                pbar.update(1)

    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Cancelling Opus scan…", file=sys.stderr)
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        if pbar:
            pbar.close()

    elapsed = time.time() - started
    out_path = Path(output or DEFAULT_OPUS_OUTPUT).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("OPUS INTEGRITY REPORT\n")
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
                f.write(f"  {rel}\n")

    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(f"ok: {oks}  warn: {warns}  error: {errs}")
        print(f"Report written to: {out_path}")
    return 1 if errs > 0 else 0


# =====================================
# Mode: Extract cover art (NEW)
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
    try:
        dir_files = os.listdir(directory)
    except OSError:
        return False

    for f in dir_files:
        ext = os.path.splitext(f)[1].lower()
        extractor = _ART_EXTRACTORS.get(ext)
        if extractor:
            data = extractor(os.path.join(directory, f))
            if data:
                return True
    return False


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
            os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS for f in files
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
# Mode: Missing art report (NEW)
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
            f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
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
# Mode: Duplicate detection (NEW)
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
            f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
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
# Mode: Tag audit (NEW)
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
    current = 0
    pbar = _make_pbar(total, "Auditing tags", quiet)

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            current += 1
            if pbar:
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

    if pbar:
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
    group.add_argument("--testFLAC", action="store_true", help="Verify FLAC files")
    group.add_argument("--testMP3", action="store_true", help="Verify MP3 files")
    group.add_argument("--testOpus", action="store_true", help="Verify Opus files via FFmpeg decode")
    group.add_argument("--extractArt", action="store_true", help="Extract embedded cover art to folder")
    group.add_argument("--missingArt", action="store_true", help="Report directories missing cover art")
    group.add_argument("--duplicates", action="store_true", help="Detect duplicate artist+album across formats")
    group.add_argument("--auditTags", action="store_true", help="Report files with incomplete tags")

    p.add_argument("--root", default=".", help="Root directory (default: current)")
    p.add_argument("--output", default=None, help="Output path")
    p.add_argument("--workers", type=int, default=4, help="Parallel workers (integrity modes)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac", help="Preferred tool (FLAC mode)")
    p.add_argument("--quiet", action="store_true", help="Minimize output")
    p.add_argument("--genres", action="store_true", help="Include album genres in library tree")
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
    try:
        raw = input(f"{label} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)
    return raw or (default or "")


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def interactive_menu() -> int:
    while True:
        _reset_terminal()
        print("\n=== getMusic.py — Menu ===")
        print("1) Build music library tree")
        print("2) Test FLAC integrity")
        print("3) Test MP3 decode errors")
        print("4) Test Opus decode errors")
        print("5) Extract cover art")
        print("6) Report missing art")
        print("7) Find duplicate albums")
        print("8) Audit tags (missing title/artist/track/genre)")
        print("q) Quit")
        try:
            choice = input("Select [1-8/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 130

        if choice in ("1", "l", "lib"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT) or DEFAULT_LIBRARY_OUTPUT
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith('y')
            write_music_library_tree(root, output, quiet=False, show_genre=show_g)
            print(f"\nMusic library written to {output}")

        elif choice in ("2", "flac"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_FLAC_OUTPUT) or DEFAULT_FLAC_OUTPUT
            workers = _prompt_int("Workers", 4)
            pref = _prompt_str("Preferred tool (flac/ffmpeg)", "flac").lower()
            run_flac_mode(root, output, workers, pref, quiet=False)

        elif choice in ("3", "mp3"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_MP3_OUTPUT) or DEFAULT_MP3_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_mp3_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )

        elif choice in ("4", "opus"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_OPUS_OUTPUT) or DEFAULT_OPUS_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_opus_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )

        elif choice in ("5", "art", "extract"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            dry = _prompt_str("Dry run? (y/N)", "N").lower().startswith('y')
            run_extract_art(root, quiet=False, dry_run=dry)

        elif choice in ("6", "missing"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_MISSING_ART_OUTPUT) or DEFAULT_MISSING_ART_OUTPUT
            run_missing_art(root, output, quiet=False)

        elif choice in ("7", "dup", "dupes"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_DUPLICATES_OUTPUT) or DEFAULT_DUPLICATES_OUTPUT
            run_duplicates(root, output, quiet=False)

        elif choice in ("8", "audit", "tags"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_TAG_AUDIT_OUTPUT) or DEFAULT_TAG_AUDIT_OUTPUT
            run_tag_audit(root, output, quiet=False)

        elif choice in ("q", "quit", "exit"):
            return 0
        else:
            print("Invalid selection.")


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        args = build_parser().parse_args(argv)
        root = os.path.abspath(args.root)

        if args.library:
            output = args.output or DEFAULT_LIBRARY_OUTPUT
            write_music_library_tree(root, output, quiet=args.quiet, show_genre=args.genres)
            return 0

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

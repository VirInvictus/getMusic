
#!/usr/bin/env python3
"""
getMusic.py — Two tools in one:
  1) Music library tree writer (tags + ratings)  ->  --library
  2) FLAC integrity checker (flac/ffmpeg)       ->  --checkFLAC

Examples:
  # Write a music library tree for the current directory
  python getMusic.py --library --root "." --output music_library.txt

  # Check FLACs under D:\Music and write problematic files to CSV
  python getMusic.py --checkFLAC --root "D:\Music" --output flac_errors.csv --workers 6 --prefer flac
"""

import argparse
import csv
import os
import re
import sys
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Iterable

# ---------- Third‑party (mutagen) ----------
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    from mutagen.asf import ASF
except Exception as e:
    print("ERROR: This script requires the 'mutagen' package. Install via: pip install mutagen", file=sys.stderr)
    raise

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma', '.aac'}

# =====================================================================
# ====================  LIBRARY TREE (from getMusic)  ==================
# =====================================================================

def _clean_song_name(filename: str) -> str:
    """Extract track number and song title from filename without double numbering."""
    name_without_ext = os.path.splitext(filename)[0]
    name_without_ext = re.sub(r'^[^-\d]*-\s*', '', name_without_ext)
    patterns = [
        r'^(?:\d+\s*[-–—]\s*)?(\d+)\.?\s*[-–—]?\s*(.+)$',
        r'^[Tt]rack\s*(\d+)\.?\s*[-–—]?\s*(.+)$',
        r'^(\d+)\s+(.+)$'
    ]
    for pattern in patterns:
        match = re.match(pattern, name_without_ext.strip())
        if match:
            track_num = match.group(1).zfill(2)
            title = match.group(2).strip()
            return f"{track_num}. {title}"
    return name_without_ext.strip()


def _normalize_rating(val):
    """Convert any numeric rating scale to 0–5 stars."""
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
    except Exception:
        pass
    return None


def _format_rating(rating):
    """Return formatted stars for a rating."""
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


def _update_progress(current, total, prefix="Progress"):
    """Display progress bar."""
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


def _count_audio_files(root_dir):
    """Count total audio files in the library."""
    total = 0
    for _, _, files in os.walk(root_dir):
        total += sum(1 for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS)
    return total


def _first_text(val):
    """
    Normalize a mutagen tag value to a plain string if possible.
    Handles lists/tuples and ASF objects.
    """
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    # ASF values sometimes have .value
    try:
        if hasattr(val, "value"):
            val = val.value
    except Exception:
        pass
    if val is None:
        return None
    return str(val).strip() or None


def _parse_track_number(val):
    """
    Parse track number from various formats:
    - "3" or "03" or "3/12"
    - MP4 'trkn': [(track, total)]
    """
    if val is None:
        return None

    # MP4 trkn -> list of tuples
    if isinstance(val, list) and val and isinstance(val[0], tuple):
        try:
            num = int(val[0][0])
            return num if num > 0 else None
        except Exception:
            return None

    s = _first_text(val)
    if not s:
        return None
    s = s.split('/')[0]
    try:
        n = int(s)
        return n if n > 0 else None
    except Exception:
        return None


def _get_title_artist_track(file_path):
    """
    Try hard to get (title, artist, trackno) from tags across formats.
    Falls back progressively and returns (None, None, None) if not found.
    """
    title = artist = None
    trackno = None

    # First pass: 'easy' mutagen (unified keys for many formats)
    try:
        easy = MutagenFile(file_path, easy=True)
        if easy and easy.tags:
            title = _first_text(easy.tags.get('title'))
            artist = _first_text(easy.tags.get('artist')) or _first_text(easy.tags.get('albumartist'))
            trackno = _parse_track_number(easy.tags.get('tracknumber'))
    except Exception:
        pass

    # If still missing, do format-specific
    try:
        audio = MutagenFile(file_path)
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

        elif isinstance(audio, (FLAC, OggVorbis)):
            tags = getattr(audio, 'tags', {}) or {}
            keys = {k.lower(): k for k in (tags.keys() if tags else [])}
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
            name_map = {k.lower(): k for k in (tags.keys() if tags else [])}
            if title is None:
                k = name_map.get('title')
                if k:
                    title = _first_text(tags.get(k))
            if artist is None:
                k = name_map.get('author') or name_map.get('wm/albumartist')
                if k:
                    artist = _first_text(tags.get(k))
            if trackno is None:
                k = name_map.get('wm/tracknumber') or name_map.get('tracknumber')
                if k:
                    trackno = _parse_track_number(tags.get(k))

    except Exception:
        pass

    return title, artist, trackno


def _get_rating(file_path):
    """Get normalized rating from audio file, prioritizing WMP POPM for MP3."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        audio = MutagenFile(file_path)
        if not audio:
            return None

        # --- MP3 ---
        if ext == '.mp3':
            try:
                id3 = ID3(file_path)
            except ID3NoHeaderError:
                return None

            # POPM frames — prefer Windows Media Player 9 Series
            for popm in id3.getall('POPM'):
                if getattr(popm, "email", "") == 'Windows Media Player 9 Series':
                    wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                    return wmp_map.get(popm.rating, _normalize_rating(popm.rating))

            # If no WMP POPM, fall back to other POPM
            for popm in id3.getall('POPM'):
                if popm.rating > 0:
                    return _normalize_rating(popm.rating)

            # TXXX frames as last resort
            for txxx in id3.getall('TXXX'):
                desc = (getattr(txxx, "desc", "") or "").lower()
                vals = getattr(txxx, "text", [])
                val = vals[0] if vals else None
                if 'rating' in desc or desc in ('rate', 'score', 'stars'):
                    if val and str(val).replace('.', '').isdigit():
                        return _normalize_rating(val)

        # --- FLAC / OGG ---
        elif isinstance(audio, (FLAC, OggVorbis)):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower() or 'score' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return _normalize_rating(val)

        # --- MP4 / M4A ---
        elif isinstance(audio, MP4):
            for key, val in (audio.tags or {}).items():
                k = key.lower() if isinstance(key, str) else str(key).lower()
                if 'rate' in k or 'rating' in k:
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return _normalize_rating(val)

        # --- WMA / ASF ---
        elif isinstance(audio, ASF):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return _normalize_rating(val)

        return None

    except Exception:
        return None


def write_music_library_tree(root_dir: str, output_file: str) -> None:
    print("Counting audio files.")
    total_files = _count_audio_files(root_dir)
    print(f"Found {total_files} audio files to process\n")

    current_file = 0
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
                f.write(f"  {connector} ALBUM: {album}\n")

                songs = sorted([
                    s for s in os.listdir(album_path)
                    if os.path.splitext(s)[1].lower() in AUDIO_EXTENSIONS
                ])

                if not songs:
                    f.write("      └── [No Audio Files Found]\n")
                    continue

                for j, song in enumerate(songs):
                    current_file += 1
                    _update_progress(current_file, total_files, "Scanning")

                    song_path = os.path.join(album_path, song)
                    title, artist_tag, trackno = _get_title_artist_track(song_path)

                    # Build display string: "03. Artist — Title"
                    if title or artist_tag:
                        parts = []
                        if trackno:
                            parts.append(f"{int(trackno):02d}.")
                        if artist_tag:
                            parts.append(artist_tag)
                        if title:
                            if artist_tag:
                                parts.append("—")
                            parts.append(title)
                        display_name = " ".join(parts).strip()
                    else:
                        display_name = _clean_song_name(song)

                    ext = os.path.splitext(song)[1].lower().strip('.')
                    rating = _get_rating(song_path)
                    rating_str = _format_rating(rating)

                    song_connector = "└──" if j == len(songs) - 1 else "├──"
                    f.write(f"      {song_connector} SONG: {display_name} ({ext}){rating_str}\n")
            f.write("\n")


# =====================================================================
# ====================  FLAC CHECKER (from testFLAC)  ==================
# =====================================================================

def _decode_bytes(b: bytes) -> str:
    # Try UTF-8 first, then Windows MBCS (OEM), then latin-1 as a last resort.
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", errors="replace")


def _run_proc(args: List[str]) -> Tuple[int, str, str]:
    # Push tools toward UTF-8 where possible, but still decode bytes ourselves.
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,            # read raw bytes; we’ll decode safely
        env=env
    )
    out_b, err_b = proc.communicate()
    out = _decode_bytes(out_b).strip()
    err = _decode_bytes(err_b).strip()
    return proc.returncode, out, err


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _test_with_flac(filepath: str) -> Tuple[bool, str]:
    code, out, err = _run_proc(["flac", "-t", "-s", filepath])
    if code == 0:
        return True, ""
    msg = err or out or f"flac exited with code {code}"
    return False, msg


def _test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    code, out, err = _run_proc(
        ["ffmpeg", "-v", "error", "-nostats", "-i", filepath, "-f", "null", "-"]
    )
    if code == 0 and not err:
        return True, ""
    if code == 0 and err:
        return False, err
    return False, err or out or f"ffmpeg exited with code {code}"


def test_flac(filepath: str, prefer: str) -> Tuple[bool, str, str]:
    """Returns (ok, method_used, error_message_if_any)."""
    if prefer == "ffmpeg":
        if _has_tool("ffmpeg"):
            ok, msg = _test_with_ffmpeg(filepath)
            if ok or not _has_tool("flac"):
                return ok, "ffmpeg", "" if ok else msg
        if _has_tool("flac"):
            ok, msg = _test_with_flac(filepath)
            return ok, "flac", "" if ok else msg
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."
    else:
        if _has_tool("flac"):
            ok, msg = _test_with_flac(filepath)
            return ok, "flac", "" if ok else msg
        if _has_tool("ffmpeg"):
            ok, msg = _test_with_ffmpeg(filepath)
            return ok, "ffmpeg", "" if ok else msg
        return False, "none", "Neither 'flac' nor 'ffmpeg' found in PATH."


def _find_flacs(root: str) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".flac"):
                yield os.path.join(dirpath, name)


def run_check_flac(root: str, output_csv: str, workers: int = 4, prefer: str = "flac") -> int:
    root_abs = os.path.abspath(root)
    flacs = list(_find_flacs(root_abs))
    total = len(flacs)

    if total == 0:
        print(f"No FLAC files found under: {root_abs}")
        return 0

    if not (_has_tool("flac") or _has_tool("ffmpeg")):
        print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one and retry.", file=sys.stderr)
        return 2

    print(f"Found {total} FLAC files under: {root_abs}")
    errors: List[Tuple[str, str, str]] = []  # (path, method, message)

    def worker(path: str) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(path, prefer)
            return path, ok, method, msg
        except Exception as e:
            return path, False, "exception", repr(e)

    checked = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(worker, pth): pth for pth in flacs}
        for fut in as_completed(futures):
            path, ok, method, msg = fut.result()
            checked += 1
            if not ok:
                errors.append((path, method, msg))
            if checked % 50 == 0 or checked == total:
                print(f"Progress: {checked}/{total} checked...", end="\r", flush=True)

    print()  # newline after progress

    if errors:
        out_path = os.path.abspath(output_csv)
        # Ensure parent dir exists
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "method", "error"])
            for row in errors:
                w.writerow(row)

        print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}")
        for pth, method, msg in errors[:5]:
            snippet = msg.replace("\n", " ")[:160]
            print(f"- {pth} [{method}] -> {snippet}{'...' if len(msg) > 160 else ''}")
        return 1

    print("✅ All FLAC files passed integrity checks.")
    return 0


# =====================================================================
# ==============================  CLI  ================================
# =====================================================================

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Music tools: library tree + FLAC checker")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--library", action="store_true", help="Generate music library tree")
    group.add_argument("--checkFLAC", action="store_true", help="Verify FLAC integrity")

    # Shared-ish options
    p.add_argument("--root", default=".", help="Root directory to scan (default: current dir)")
    p.add_argument("--output", default=None, help="Output file path. "
                   "For --library: defaults to 'music_library.txt'. For --checkFLAC: defaults to 'flac_errors.csv'.")
    # Only for checkFLAC
    p.add_argument("--workers", type=int, default=4, help="(checkFLAC) Parallel workers (default: 4)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac",
                   help="(checkFLAC) Preferred tester if both available (default: flac)")

    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    if args.library:
        out = args.output or "music_library.txt"
        root_abs = os.path.abspath(args.root)
        print(f"Scanning music library in: {root_abs}")
        write_music_library_tree(root_abs, out)
        print(f"\nMusic library written to {os.path.abspath(out)}")
        return 0

    if args.checkFLAC:
        out = args.output or "flac_errors.csv"
        return run_check_flac(args.root, out, args.workers, args.prefer)

    # Should never reach here due to mutually exclusive group
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

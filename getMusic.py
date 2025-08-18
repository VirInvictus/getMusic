#!/usr/bin/env python3
# filepath: getMusic.py
"""
Merged tool: Music library tree + FLAC integrity checker.

Usage:
  # Build a text tree of your music library (default if no flag is given)
  python getMusic.py --library --root "." --output music_library.txt

  # Verify FLAC files and write failures to CSV
  python getMusic.py --testFLAC --root "." --output flac_errors.csv --workers 4 --prefer flac

Notes:
  - --root and --output apply to both modes (with different defaults).
  - --workers and --prefer apply only to --testFLAC.
  - If started with no args, an interactive menu is shown.
  - Ctrl-C (SIGINT) now cleanly cancels both modes with exit code 130.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Iterable, Optional, Dict

# --- Mutagen imports for library mode ---
from mutagen import File as MutagenFile
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
from mutagen.asf import ASF

# =====================================
# Shared CLI defaults
# =====================================
DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.csv"

# =====================================
# Library mode (original getMusic.py)
# =====================================
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma', '.aac'}


def clean_song_name(filename: str) -> str:
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


def normalize_rating(val) -> Optional[float]:
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


# ---------- Tag Helpers ----------

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
    if val is None:
        return None
    return str(val).strip() or None


def _parse_track_number(val) -> Optional[int]:
    if val is None:
        return None
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


def get_title_artist_track(file_path: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    title = artist = None
    trackno: Optional[int] = None

    try:
        easy = MutagenFile(file_path, easy=True)
        if easy and easy.tags:
            title = _first_text(easy.tags.get('title'))
            artist = _first_text(easy.tags.get('artist')) or _first_text(easy.tags.get('albumartist'))
            trackno = _parse_track_number(easy.tags.get('tracknumber'))
    except Exception:
        pass

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


def get_rating(file_path: str) -> Optional[float]:
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
        elif isinstance(audio, (FLAC, OggVorbis)):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower() or 'score' in key.lower():
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


def write_music_library_tree(root_dir: str, output_file: str) -> None:
    print("Counting audio files.")
    total_files = count_audio_files(root_dir)
    print(f"Found {total_files} audio files to process\n")

    current_file = 0
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
                        update_progress(current_file, total_files, "Scanning")

                        song_path = os.path.join(album_path, song)
                        title, artist_tag, trackno = get_title_artist_track(song_path)

                        if title or artist_tag:
                            parts: List[str] = []
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
                            display_name = clean_song_name(song)

                        ext = os.path.splitext(song)[1].lower().strip('.')
                        rating = get_rating(song_path)
                        rating_str = format_rating(rating)

                        song_connector = "└──" if j == len(songs) - 1 else "├──"
                        f.write(f"      {song_connector} SONG: {display_name} ({ext}){rating_str}\n")
                f.write("\n")
    except KeyboardInterrupt:
        print("\nInterrupted by user. Library scan cancelled.")
        return


# =====================================
# FLAC integrity mode (original testFLAC.py)
# =====================================

def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", errors="replace")


def run_proc(args: List[str]) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env,
    )
    try:
        out_b, err_b = proc.communicate()
    except KeyboardInterrupt:
        # Why: ensure child processes don't continue after Ctrl-C
        try:
            proc.kill()
        finally:
            proc.wait()
        raise
    out = _decode_bytes(out_b).strip()
    err = _decode_bytes(err_b).strip()
    return proc.returncode, out, err


def has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def test_with_flac(filepath: str) -> Tuple[bool, str]:
    code, out, err = run_proc(["flac", "-t", "-s", filepath])
    if code == 0:
        return True, ""
    msg = err or out or f"flac exited with code {code}"
    return False, msg


def test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    code, out, err = run_proc(["ffmpeg", "-v", "error", "-nostats", "-i", filepath, "-f", "null", "-"])
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


def find_flacs(root: str) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".flac"):
                yield os.path.join(dirpath, name)


def run_flac_mode(root: str, output: str, workers: int, prefer: str) -> int:
    """Scan FLACs with a progress bar. 0=ok, 1=errors found, 2=env error, 130=cancelled"""
    root = os.path.abspath(root)
    flacs = list(find_flacs(root))
    total = len(flacs)

    if total == 0:
        print(f"No FLAC files found under: {root}")
        return 0

    if not (has_tool("flac") or has_tool("ffmpeg")):
        print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one and retry.", file=sys.stderr)
        return 2

    print(f"Found {total} FLAC files under: {root}")

    errors: List[Tuple[str, str, str]] = []

    def worker(path: str) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(path, prefer)
            return path, ok, method, msg
        except KeyboardInterrupt:
            # Let Ctrl-C bubble up to cancel the pool promptly
            raise
        except Exception as e:
            return path, False, "exception", repr(e)

    checked = 0
    update_progress(checked, total, prefix="Testing FLACs")

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
            update_progress(checked, total, prefix="Testing FLACs")
    except KeyboardInterrupt:
        print("\nInterrupted by user. Cancelling FLAC checks...")
        if ex is not None:
            # Cancel tasks that have not started
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)

    if errors:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "method", "error"])
            for row in errors:
                w.writerow(row)
        print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}")
        for pth, method, msg in errors[:5]:
            snippet = msg.replace("\r", " ").replace("\n", " ")[:160]
            print(f"- {pth} [{method}] -> {snippet}{'...' if len(msg) > 160 else ''}")
        return 1

    print("✅ All FLAC files passed integrity checks.")
    return 0


# =====================================
# CLI wiring + interactive menu
# =====================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Music library tree and FLAC integrity checker")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--library", action="store_true", help="Generate library tree")
    group.add_argument("--testFLAC", action="store_true", help="Verify FLAC files and report failures")

    p.add_argument("--root", default=".", help="Root directory to scan (default: current dir)")
    p.add_argument("--output", default=None, help="Output path (library: text, testFLAC: CSV)")
    p.add_argument("--workers", type=int, default=4, help="Parallel workers for --testFLAC (default: 4)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac",
                   help="Preferred tester if both available (for --testFLAC)")
    return p


def _prompt_str(label: str, default: Optional[str]) -> str:
    try:
        raw = input(f"{label} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(130)
    return raw or (default or "")


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def interactive_menu() -> int:
    last_exit = 0
    while True:
        print("\n=== getMusic.py — Menu ===")
        print("1) Build music library tree")
        print("2) Test FLAC integrity")
        print("q) Quit")
        try:
            choice = input("Select an option [1/2/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 130

        if choice in ("1", "l", "lib", "library"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT) or DEFAULT_LIBRARY_OUTPUT
            print(f"\nScanning music library in: {root}")
            try:
                write_music_library_tree(root, output)
                print(f"\nMusic library written to {output}")
                last_exit = 0
            except KeyboardInterrupt:
                print("\nInterrupted by user. Returning to menu.")
                last_exit = 130
        elif choice in ("2", "t", "f", "flac", "test", "testflac"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("CSV output file", DEFAULT_FLAC_OUTPUT) or DEFAULT_FLAC_OUTPUT
            workers = _prompt_int("Workers", 4)
            pref = _prompt_str("Preferred tool (flac/ffmpeg)", "flac").lower()
            if pref not in ("flac", "ffmpeg"):
                pref = "flac"
            code = run_flac_mode(root=root, output=output, workers=max(1, workers), prefer=pref)
            if code == 130:
                print("Returning to menu.")
            last_exit = code
        elif choice in ("q", "quit", "exit"):
            return last_exit
        else:
            print("Invalid selection. Try again.")


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) == 0:
        return interactive_menu()

    try:
        args = build_parser().parse_args(argv)

        if args.library:
            root = os.path.abspath(args.root)
            output = args.output or DEFAULT_LIBRARY_OUTPUT
            print(f"Scanning music library in: {root}")
            write_music_library_tree(root, output)
            print(f"\nMusic library written to {output}")
            return 0

        if args.testFLAC:
            root = os.path.abspath(args.root)
            output = args.output or DEFAULT_FLAC_OUTPUT
            return run_flac_mode(root=root, output=output, workers=args.workers, prefer=args.prefer)

        build_parser().print_help()
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# filepath: get_music.py
"""
Merged tool: Music library tree + FLAC integrity checker + MP3 decode checker.

Usage:
  # 1) Build a text tree of your music library (default if no flag is given)
  python get_music.py --library --root "." --output music_library.txt

  # 2) Verify FLAC files and write failures to CSV
  python get_music.py --testFLAC --root "." --output flac_errors.csv --workers 4 --prefer flac

  # 3) Verify MP3s by trying to decode with FFmpeg; write only errors/warnings by default
  python get_music.py --testMP3 --root "." --output mp3_scan_results.csv --workers 4 --only-errors
  # Include all rows (OK too):
  python get_music.py --testMP3 --no-only-errors
  # or
  python get_music.py --testMP3 --verbose

Notes:
  - --root and --output apply to all modes (with different defaults per mode).
  - --workers applies to FLAC and MP3 modes. --prefer applies to FLAC only.
  - MP3 mode accepts --ffmpeg, --only-errors/--no-only-errors, --verbose.
  - --quiet now applies to **all** modes and also hides progress bars.
  - If started with no args, an interactive menu is shown.
  - Ctrl-C (SIGINT) cleanly cancels modes with exit code 130.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple, List, Iterable, Optional, Dict, Any

# --- Mutagen imports for library + tag helpers ---
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    from mutagen.asf import ASF
    HAVE_MUTAGEN_BASE = True
except Exception:
    HAVE_MUTAGEN_BASE = False

# MP3 mode mutagen import (optional)
try:
    from mutagen.mp3 import MP3 as MUTAGEN_MP3  # type: ignore
    HAVE_MUTAGEN_MP3 = True
except Exception:
    HAVE_MUTAGEN_MP3 = False

# tqdm for nicer progress (optional)
try:
    from tqdm import tqdm  # type: ignore
    HAVE_TQDM = True
except Exception:
    HAVE_TQDM = False

# =====================================
# Shared CLI defaults
# =====================================
DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.csv"
DEFAULT_MP3_OUTPUT = "mp3_scan_results.csv"

# =====================================
# Library mode (original get_music.py)
# =====================================
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma', '.aac'}


def clean_song_name(filename: str) -> str:
    name_without_ext = os.path.splitext(filename)[0]
    name_without_ext = re.sub(r'^[^\-\d]*-\s*', '', name_without_ext)
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


# Lightweight fallback progress for when tqdm isn't available

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

    if HAVE_MUTAGEN_BASE:
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


def write_music_library_tree(root_dir: str, output_file: str, *, quiet: bool = False) -> None:
    root_dir = os.path.abspath(root_dir)
    total_files = count_audio_files(root_dir)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {root_dir}\n")

    current_file = 0
    pbar = None
    if HAVE_TQDM and not quiet:
        # Why: align behavior with mp3scan progress style
        pbar = tqdm(total=total_files, unit="file", desc="Scanning library", dynamic_ncols=True)

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
                        if pbar:
                            pbar.update(1)
                        else:
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
        if pbar:
            pbar.close()
        if not quiet:
            print("\nInterrupted by user. Library scan cancelled.")
        return
    finally:
        if pbar:
            pbar.close()


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


def run_flac_mode(root: str, output: str, workers: int, prefer: str, *, quiet: bool = False) -> int:
    root = os.path.abspath(root)
    flacs = list(find_flacs(root))
    total = len(flacs)

    if total == 0:
        if not quiet:
            print(f"No FLAC files found under: {root}")
        return 0

    if not (has_tool("flac") or has_tool("ffmpeg")):
        if not quiet:
            print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one and retry.", file=sys.stderr)
        return 2

    if not quiet:
        print(f"Found {total} FLAC files under: {root}")

    errors: List[Tuple[str, str, str]] = []

    def worker(path: str) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(path, prefer)
            return path, ok, method, msg
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return path, False, "exception", repr(e)

    checked = 0
    pbar = None
    if HAVE_TQDM and not quiet:
        pbar = tqdm(total=total, unit="file", desc="Testing FLACs", dynamic_ncols=True)

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
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "method", "error"])
            for row in errors:
                w.writerow(row)
        if not quiet:
            print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}")
            for pth, method, msg in errors[:5]:
                snippet = msg.replace("\r", " ").replace("\n", " ")[:160]
                print(f"- {pth} [{method}] -> {snippet}{'...' if len(msg) > 160 else ''}")
        return 1

    if not quiet:
        print("✅ All FLAC files passed integrity checks.")
    return 0


# =====================================
# MP3 decode mode (ported from mp3scan.py)
# =====================================

def _find_ffmpeg(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        p = Path(explicit_path)
        return str(p) if p.exists() else None
    return shutil.which("ffmpeg")


def _find_mp3s(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    for base in paths:
        base = base.expanduser().resolve()
        if base.is_file() and base.suffix.lower() == ".mp3":
            out.append(base)
        elif base.is_dir():
            for root, _, files in os.walk(base):
                for fn in files:
                    if fn.lower().endswith(".mp3"):
                        out.append(Path(root) / fn)
    return out


def _mutagen_header_info(path: Path) -> Dict[str, Any]:
    if not HAVE_MUTAGEN_MP3:
        return {}
    try:
        audio = MUTAGEN_MP3(path)
        info = audio.info
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
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        return False, f"FFmpeg invocation failed: {e!r}"
    stderr = (proc.stderr or "").strip()
    if stderr:
        return False, stderr
    return True, "decode ok"


def _scan_one_mp3(path: Path, ffmpeg_path: Optional[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "path": str(path),
        "size_bytes": None,
        "status": "ok",
        "details": "",
        "duration_s": None,
        "bitrate_kbps": None,
        "sample_rate_hz": None,
        "mode": None,
        "vbr_mode": None,
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


def _rotated_path(p: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return p.parent / f"{p.stem}-{ts}{p.suffix}"


def _write_header(csv_path: Path, fieldnames: List[str], *, quiet: bool = False) -> Tuple[csv.DictWriter, Path]:
    if csv_path.suffix == "":
        csv_path.mkdir(parents=True, exist_ok=True)
        csv_path = csv_path / DEFAULT_MP3_OUTPUT

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    def _open(target: Path) -> Tuple[csv.DictWriter, Path]:
        f = target.open("w", newline="", encoding="utf-8")
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w._file_handle = f  # type: ignore[attr-defined]
        return w, target

    try:
        return _open(csv_path)
    except PermissionError:
        rotated = _rotated_path(csv_path)
        if not quiet:
            print(f"[warn] Can't write to '{csv_path}'. Using '{rotated}' instead.", file=sys.stderr)
        return _open(rotated)
    except IsADirectoryError:
        fallback = csv_path / DEFAULT_MP3_OUTPUT
        if not quiet:
            print(f"[warn] Output path is a directory. Writing to '{fallback}'.", file=sys.stderr)
        return _open(fallback)


def _close_writer(w: csv.DictWriter) -> None:
    fh = getattr(w, "_file_handle", None)
    if fh:
        try:
            fh.flush(); fh.close()
        except Exception:
            pass


def run_mp3_mode(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    paths = [Path(os.path.abspath(root))]
    ffmpeg_path = _find_ffmpeg(ffmpeg)

    if not ffmpeg_path and not quiet:
        print("[warn] FFmpeg not found. Install it or pass --ffmpeg path\\to\\ffmpeg.exe", file=sys.stderr)

    targets = _find_mp3s(paths)
    if not targets:
        if not quiet:
            print("No .mp3 files found under provided path(s).", file=sys.stderr)
        return 0

    fieldnames = [
        "path",
        "status",
        "details",
        "size_bytes",
        "duration_s",
        "bitrate_kbps",
        "sample_rate_hz",
        "mode",
        "vbr_mode",
    ]
    out_path = Path(output or DEFAULT_MP3_OUTPUT).expanduser().resolve()
    writer, out_path = _write_header(out_path, fieldnames, quiet=quiet)

    started = time.time()
    oks = warns = errs = 0
    written = 0

    pbar = None
    if HAVE_TQDM and not quiet:
        pbar = tqdm(total=len(targets), unit="file", desc="Scanning MP3s", dynamic_ncols=True)

    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

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

            if verbose:
                only_errors = False
                quiet = False

            if not (only_errors and status == "ok"):
                writer.writerow(row)
                written += 1
                try:
                    writer._file_handle.flush()  # type: ignore[attr-defined]
                except Exception:
                    pass

            if pbar:
                pbar.update(1)

    except KeyboardInterrupt:
        # Clean, Windows-friendly cancellation: stop workers, close bar, finalize CSV, exit 130.
        if not quiet:
            print("\nInterrupted by user. Cancelling MP3 scan…", file=sys.stderr)
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        if pbar:
            pbar.close()
        _close_writer(writer)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        if pbar:
            pbar.close()
        _close_writer(writer)

    elapsed = time.time() - started
    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(f"ok: {oks}  warn: {warns}  error: {errs}")
        print(f"written to CSV: {written}  (path: {out_path})")
        if only_errors and oks:
            print("[note] OK rows omitted; use --no-only-errors or --verbose to include them.", file=sys.stderr)
        if not HAVE_MUTAGEN_MP3:
            print("[note] Mutagen not installed; header fields may be empty. Install with: pip install mutagen", file=sys.stderr)

    return 1 if errs > 0 else 0


# =====================================
# CLI wiring + interactive menu
# =====================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Music library tree, FLAC integrity, and MP3 decode checker")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--library", action="store_true", help="Generate library tree")
    group.add_argument("--testFLAC", action="store_true", help="Verify FLAC files and report failures")
    group.add_argument("--testMP3", action="store_true", help="Verify MP3 files and report decode errors/warnings")

    p.add_argument("--root", default=".", help="Root directory to scan (default: current dir)")
    p.add_argument("--output", default=None, help="Output path (library: text, FLAC/MP3: CSV)")
    p.add_argument("--workers", type=int, default=4, help="Parallel workers for FLAC/MP3 (default: 4)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac",
                   help="Preferred tester if both available (for --testFLAC)")

    # Global quiet toggle (all modes)
    p.add_argument("--quiet", action="store_true", help="Reduce console output and hide progress bars (all modes)")

    # MP3-mode specific knobs (safe to expose globally)
    try:
        BooleanFlag = argparse.BooleanOptionalAction  # py>=3.9
    except AttributeError:  # pragma: no cover
        BooleanFlag = None  # type: ignore

    if BooleanFlag:
        p.add_argument("--only-errors", dest="only_errors", action=BooleanFlag, default=True,
                       help="Write only rows with status != ok (MP3 mode; default: true)")
    else:
        p.add_argument("--only-errors", dest="only_errors", action="store_true", default=True,
                       help="Write only rows with status != ok (MP3 mode; default: true)")

    p.add_argument("--ffmpeg", default=None, help="Path to ffmpeg (for --testMP3; otherwise uses PATH)")
    p.add_argument("--verbose", action="store_true", help="Verbose output; include OK rows (MP3 mode)")
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
        print("\n=== get_music.py — Menu ===")
        print("1) Build music library tree")
        print("2) Test FLAC integrity")
        print("3) Test MP3 decode errors")
        print("q) Quit")
        try:
            choice = input("Select an option [1/2/3/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 130

        if choice in ("1", "l", "lib", "library"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT) or DEFAULT_LIBRARY_OUTPUT
            print(f"\nScanning music library in: {root}")
            try:
                write_music_library_tree(root, output, quiet=False)
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
            code = run_flac_mode(root=root, output=output, workers=max(1, workers), prefer=pref, quiet=False)
            if code == 130:
                print("Returning to menu.")
            last_exit = code
        elif choice in ("3", "m", "mp3", "testmp3"):
            root = os.path.abspath(os.path.expanduser(_prompt_str("Root directory", ".")))
            output = _prompt_str("CSV output file", DEFAULT_MP3_OUTPUT) or DEFAULT_MP3_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            ffmpeg = _prompt_str("Path to ffmpeg (blank to use PATH)", "") or None
            code = run_mp3_mode(
                root=root,
                output=output,
                workers=max(1, workers),
                ffmpeg=ffmpeg,
                only_errors=not include_ok,
                verbose=include_ok,
                quiet=False,
            )
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
            if not args.quiet:
                print(f"Scanning music library in: {root}")
            write_music_library_tree(root, output, quiet=args.quiet)
            if not args.quiet:
                print(f"\nMusic library written to {output}")
            return 0

        if args.testFLAC:
            root = os.path.abspath(args.root)
            output = args.output or DEFAULT_FLAC_OUTPUT
            return run_flac_mode(root=root, output=output, workers=args.workers, prefer=args.prefer, quiet=args.quiet)

        if args.testMP3:
            root = os.path.abspath(args.root)
            output = args.output or DEFAULT_MP3_OUTPUT
            return run_mp3_mode(
                root=root,
                output=output,
                workers=args.workers,
                ffmpeg=args.ffmpeg,
                only_errors=args.only_errors,
                verbose=args.verbose,
                quiet=args.quiet,
            )

        build_parser().print_help()
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

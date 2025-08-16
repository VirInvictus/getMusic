#!/usr/bin/env python3
"""
getMusic.py — Two tools in one:
  1) Music library export (tags + tech info + rating) -> --library
  2) FLAC integrity checker (flac/ffmpeg)            -> --checkFLAC

Examples:
  # Write a music library table for the current directory
  python getMusic.py --library --root "." --output music_library.tsv

  # Check FLACs under D:\\Music and save problematic files to CSV
  python getMusic.py --checkFLAC --root "D:\\Music" --output flac_errors.csv --workers 6 --prefer flac
"""

import argparse
import csv
import os
import sys
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Iterable, Optional, Dict, Any

# --- Optional metadata libs for --library ---
try:
    from mutagen import File as MutagenFile  # type: ignore
    from mutagen.id3 import ID3, POPM, TXXX  # type: ignore
    from mutagen.flac import FLAC            # type: ignore
    from mutagen.mp4 import MP4              # type: ignore
    from mutagen.asf import ASF              # type: ignore
    HAVE_MUTAGEN = True
except Exception:
    HAVE_MUTAGEN = False
    MutagenFile = None  # type: ignore

# -------------------------
# Utilities
# -------------------------

def _decode_bytes(b: bytes) -> str:
    """Decode tool output robustly across platforms."""
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", errors="replace")


def _run_proc(args: List[str]) -> Tuple[int, str, str]:
    """Run a subprocess and return (exit_code, stdout_text, stderr_text)."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env
    )
    out_b, err_b = proc.communicate()
    out = _decode_bytes(out_b).strip()
    err = _decode_bytes(err_b).strip()
    return proc.returncode, out, err


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _update_progress(current: int, total: int, prefix: str = "Progress") -> None:
    """Single-line progress bar like: 'Progress: |██████░░░░|  60/100 (60.0%)'"""
    if total <= 0:
        return
    percent = (current / total) * 100.0
    bar_length = 40
    filled = int(bar_length * current // total)
    bar = "█" * filled + "░" * (bar_length - filled)
    sys.stdout.write(f"\r{prefix}: |{bar}| {current}/{total} ({percent:.1f}%)")
    sys.stdout.flush()
    if current >= total:
        print()  # newline


# -------------------------
# FLAC integrity checking
# -------------------------

def _test_with_flac(filepath: str) -> Tuple[bool, str]:
    code, out, err = _run_proc(["flac", "-t", "-s", filepath])
    if code == 0:
        return True, ""
    return False, err or out or f"flac exited with code {code}"


def _test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    code, out, err = _run_proc(["ffmpeg", "-v", "error", "-i", filepath, "-f", "null", "-"])
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
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."


def _find_flacs(root: str) -> Iterable[str]:
    """Fast directory traversal using scandir for .flac files."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".flac"):
                            yield entry.path
                    except PermissionError:
                        continue
        except PermissionError:
            continue


def run_check_flac(root: str, output_csv: str, workers: int = 0, prefer: str = "flac") -> int:
    root_abs = os.path.abspath(root)

    flac_ok = _has_tool("flac")
    ffmpeg_ok = _has_tool("ffmpeg")
    if not (flac_ok or ffmpeg_ok):
        print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one and retry.", file=sys.stderr)
        return 2

    flacs = list(_find_flacs(root_abs))
    total = len(flacs)

    if total == 0:
        print(f"No FLAC files found under: {root_abs}")
        return 0

    print(f"Found {total} FLAC files under: {root_abs}\n")

    progress_lock = threading.Lock()
    checked = 0
    errors: List[Tuple[str, str, str]] = []  # (path, method, message)

    def worker(path: str) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(path, prefer)
            return path, ok, method, msg
        except Exception as e:
            return path, False, "exception", repr(e)

    max_workers = max(1, workers or ((os.cpu_count() or 4) * 2))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, p): p for p in flacs}
        for fut in as_completed(futures):
            path, ok, method, msg = fut.result()
            if not ok:
                errors.append((path, method, msg))
            with progress_lock:
                checked += 1
                _update_progress(checked, total, prefix="Checking FLACs")

    if errors:
        out_path = os.path.abspath(output_csv)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "method", "error"])
            w.writerows(errors)

        print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}")
        for pth, method, msg in errors[:5]:
            snippet = (msg or "").replace("\n", " ")[:160]
            print(f"- {pth} [{method}] -> {snippet}{'...' if msg and len(msg) > 160 else ''}")
        return 1

    print("✅ All FLAC files passed integrity checks.")
    return 0


# -------------------------
# Library writer (richer fields)
# -------------------------

_MUSIC_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac", ".aiff", ".aif"}

def _is_music(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _MUSIC_EXTS


def _find_music(root: str) -> Iterable[str]:
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False) and _is_music(entry.name):
                            yield entry.path
                    except PermissionError:
                        continue
        except PermissionError:
            continue


def _norm_one(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    if val is None:
        return None
    try:
        # Some mutagen types have .text or .value; coerce to str
        if hasattr(val, "text"):
            v = val.text
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            return None if v is None else str(v)
        if hasattr(val, "value"):
            return None if val.value is None else str(val.value)
        return str(val)
    except Exception:
        return None


def _id3_popm_to_5star(popm: POPM) -> Optional[float]:
    """Map ID3 POPM rating (0..255) to 0..5 stars."""
    try:
        raw = getattr(popm, "rating", None)
        if raw is None:
            return None
        return round((raw / 255.0) * 5.0, 2)
    except Exception:
        return None


def _guess_rating(tags: Any) -> Optional[float]:
    """Try to normalize a 'rating' across formats into 0..5 float."""
    try:
        # ID3 (MP3)
        if isinstance(tags, ID3):
            # POPM frames
            for k, v in tags.items():
                if isinstance(v, POPM):
                    r = _id3_popm_to_5star(v)
                    if r is not None:
                        return r
            # TXXX custom fields
            for k, v in tags.items():
                if isinstance(v, TXXX) and v.desc and "rating" in v.desc.lower():
                    try:
                        num = float(_norm_one(v))
                        # Heuristics: if >5 it's probably 0..100
                        if num > 5:
                            num = num / 20.0
                        return max(0.0, min(5.0, num))
                    except Exception:
                        continue

        # Vorbis/FLAC/Opus (dict-like keys)
        if hasattr(tags, "keys"):
            for key in ("RATING", "FMPS_RATING", "RATING WMP", "ROONTRACKTAG"):
                for k in list(tags.keys()):
                    if str(k).upper() == key:
                        try:
                            val = _norm_one(tags[k])
                            if val is None:
                                continue
                            num = float(val)
                            if num > 5:
                                num = num / 20.0
                            return max(0.0, min(5.0, num))
                        except Exception:
                            continue

        # MP4 (Apple)
        if isinstance(tags, dict) and any(isinstance(x, bytes) for x in tags.keys()):
            # Common atoms: 'rate' or custom ---- ratings (varies)
            for atom in (b"rate",):
                if atom in tags:
                    try:
                        v = tags.get(atom)
                        v = v[0] if isinstance(v, list) else v
                        num = float(v)
                        if num > 5:
                            num = num / 20.0
                        return max(0.0, min(5.0, num))
                    except Exception:
                        continue

        # ASF/WMA: WM/SharedUserRating is 1..99 (5-star ~= 99)
        if isinstance(tags, dict):
            for k in tags.keys():
                ks = str(k)
                if "WM/SharedUserRating" in ks:
                    try:
                        v = tags[k]
                        v = v[0] if isinstance(v, list) else v
                        num = float(v)
                        # Map 1..99 to 0..5
                        return round((num / 99.0) * 5.0, 2)
                    except Exception:
                        continue
    except Exception:
        pass
    return None


def _read_tags_and_info(path: str) -> Dict[str, Any]:
    """
    Rich tag reader:
      - Basic tags: artist, album, albumartist, title, track, disc, date, genre
      - Rating normalized to 0..5 when found
      - ReplayGain (track gain/peak) if present
      - Technical: duration, bitrate_kbps, sample_rate, channels, size_bytes, ext
    """
    d: Dict[str, Any] = {
        "file": path,
        "ext": os.path.splitext(path)[1].lower(),
        "size_bytes": None,
        "duration_sec": None,
        "bitrate_kbps": None,
        "sample_rate": None,
        "channels": None,
        "artist": None,
        "album": None,
        "albumartist": None,
        "title": None,
        "track": None,
        "disc": None,
        "date": None,
        "genre": None,
        "rg_track_gain": None,
        "rg_track_peak": None,
        "rating": None,
    }
    try:
        st = os.stat(path)
        d["size_bytes"] = st.st_size
    except Exception:
        pass

    if not HAVE_MUTAGEN:
        # No metadata lib; return bare minimum
        base = os.path.basename(path)
        d["title"] = os.path.splitext(base)[0]
        return d

    try:
        audio = MutagenFile(path, easy=False)
    except Exception:
        audio = None

    # Technical info
    try:
        if audio and getattr(audio, "info", None):
            info = audio.info
            dur = getattr(info, "length", None)
            br = getattr(info, "bitrate", None)
            sr = getattr(info, "sample_rate", None)
            ch = getattr(info, "channels", None)
            d["duration_sec"] = float(dur) if dur is not None else None
            d["bitrate_kbps"] = round(br / 1000.0) if br else None
            d["sample_rate"] = int(sr) if sr else None
            d["channels"] = int(ch) if ch else None
    except Exception:
        pass

    # Tags vary wildly across formats
    tags = getattr(audio, "tags", None)

    # Common "easy" keys if present
    def pick(keys):
        for k in keys:
            try:
                if hasattr(tags, "get"):
                    v = tags.get(k)
                else:
                    v = tags[k] if k in tags else None
            except Exception:
                v = None
            vv = _norm_one(v)
            if vv:
                return vv
        return None

    # Basic
    d["artist"]       = pick(["artist", "ARTIST", "\xa9ART"])
    d["album"]        = pick(["album", "ALBUM", "\xa9alb"])
    d["albumartist"]  = pick(["albumartist", "ALBUMARTIST", "aART"])
    d["title"]        = pick(["title", "TITLE", "\xa9nam"]) or d["title"]
    d["track"]        = pick(["tracknumber", "TRACKNUMBER", "trkn", "TRACK"])
    d["disc"]         = pick(["discnumber", "DISCNUMBER", "disk"])
    d["date"]         = pick(["date", "year", "DATE", "\xa9day"])
    d["genre"]        = pick(["genre", "GENRE", "\xa9gen"])

    # ReplayGain
    d["rg_track_gain"] = pick(["replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN"])
    d["rg_track_peak"] = pick(["replaygain_track_peak", "REPLAYGAIN_TRACK_PEAK"])

    # Format-specific extras + rating
    try:
        if isinstance(audio, FLAC):
            # Vorbis comments already handled by pick(); rating heuristic:
            d["rating"] = _guess_rating(audio.tags)
        elif isinstance(audio, MP4):
            d["rating"] = _guess_rating(audio.tags)
        elif isinstance(audio, ASF):
            d["rating"] = _guess_rating(audio.tags)
        elif isinstance(audio, ID3) or isinstance(tags, ID3):
            d["rating"] = _guess_rating(tags)
    except Exception:
        pass

    return d


LIBRARY_COLUMNS = [
    "relpath", "ext", "size_bytes",
    "duration_sec", "bitrate_kbps", "sample_rate", "channels",
    "artist", "album", "albumartist", "title", "track", "disc", "date", "genre",
    "rg_track_gain", "rg_track_peak", "rating"
]


def write_music_library_table(root: str, output_path: str) -> None:
    root_abs = os.path.abspath(root)
    files = list(_find_music(root_abs))
    total = len(files)
    print(f"Scanning music under: {root_abs} ({total} files)\n")

    # Decide format by extension of output
    delim = "\t" if output_path.lower().endswith((".tsv", ".tab")) else ","
    is_csv = delim == ","
    if is_csv:
        print("Note: Writing CSV. Use --output with .tsv for tab-delimited (safer for titles with commas).")

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        if is_csv:
            w = csv.writer(f)
            w.writerow(LIBRARY_COLUMNS)
        else:
            # Manual TSV to keep control over quoting
            f.write("\t".join(LIBRARY_COLUMNS) + "\n")

        for i, path in enumerate(files, 1):
            rec = _read_tags_and_info(path)
            rel = os.path.relpath(path, root_abs)
            row = [
                rel,
                rec.get("ext"),
                rec.get("size_bytes"),
                rec.get("duration_sec"),
                rec.get("bitrate_kbps"),
                rec.get("sample_rate"),
                rec.get("channels"),
                rec.get("artist"),
                rec.get("album"),
                rec.get("albumartist"),
                rec.get("title"),
                rec.get("track"),
                rec.get("disc"),
                rec.get("date"),
                rec.get("genre"),
                rec.get("rg_track_gain"),
                rec.get("rg_track_peak"),
                rec.get("rating"),
            ]
            if is_csv:
                w.writerow(row)
            else:
                f.write("\t".join("" if v is None else str(v) for v in row) + "\n")
            _update_progress(i, total, prefix="Building library")
    print(f"\nMusic library written to {os.path.abspath(output_path)}")


# -------------------------
# CLI
# -------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Music library exporter and FLAC checker")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--library", action="store_true", help="Export a music library table")
    g.add_argument("--checkFLAC", action="store_true", help="Verify FLAC files using flac/ffmpeg")

    p.add_argument("--root", default=".", help="Root folder to scan (default: current directory)")
    p.add_argument("--output", help="Output path (.tsv/.csv). Defaults: library=music_library.tsv, checkFLAC=flac_errors.csv")
    p.add_argument("--workers", type=int, default=0, help="Worker threads for --checkFLAC (0 = auto)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac", help="Preferred tool for FLAC testing")
    return p


def main(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)

    root = args.root
    if args.library:
        out = args.output or "music_library.tsv"
        write_music_library_table(root, out)
        return 0

    if args.checkFLAC:
        out = args.output or "flac_errors.csv"
        return run_check_flac(root, out, args.workers, args.prefer)

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
check_flacs.py — Verify FLAC integrity recursively and report failures to CSV.

Usage:
  python check_flacs.py --root "D:\\Music" --output flac_errors.csv --workers 6 --prefer flac
"""

import argparse
import csv
import os
import sys
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Iterable

# ---------- Subprocess helpers (robust decoding on Windows) ----------

def _decode_bytes(b: bytes) -> str:
    # Try UTF-8 first, then Windows MBCS (OEM), then latin-1 as a last resort.
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", errors="replace")

def run_proc(args: List[str]) -> Tuple[int, str, str]:
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

def has_tool(name: str) -> bool:
    return shutil.which(name) is not None

# ---------- FLAC testers ----------

def test_with_flac(filepath: str) -> Tuple[bool, str]:
    # flac -t = test; -s = silent
    code, out, err = run_proc(["flac", "-t", "-s", filepath])
    if code == 0:
        return True, ""
    # Some builds print to stderr; surface whichever has content.
    msg = err or out or f"flac exited with code {code}"
    return False, msg

def test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    # Decode to null sink. Treat any stderr at -v error as a failure.
    code, out, err = run_proc(
        ["ffmpeg", "-v", "error", "-nostats", "-i", filepath, "-f", "null", "-"]
    )
    if code == 0 and not err:
        return True, ""
    if code == 0 and err:
        return False, err
    return False, err or out or f"ffmpeg exited with code {code}"

def test_flac(filepath: str, prefer: str) -> Tuple[bool, str, str]:
    """
    Returns (ok, method_used, error_message_if_any)
    """
    if prefer == "ffmpeg":
        if has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            if ok or not has_tool("flac"):
                return ok, "ffmpeg", "" if ok else msg
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", "" if ok else msg
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."
    else:
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", "" if ok else msg
        if has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            return ok, "ffmpeg", "" if ok else msg
        return False, "none", "Neither 'flac' nor 'ffmpeg' found in PATH."

# ---------- File crawling ----------

def find_flacs(root: str) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".flac"):
                yield os.path.join(dirpath, name)

# ---------- Main ----------

def main() -> int:
    p = argparse.ArgumentParser(description="Verify FLAC files recursively and report failures.")
    p.add_argument("--root", default=".", help="Root directory to scan (default: current dir)")
    p.add_argument("--output", default="flac_errors.csv", help="CSV output path (default: flac_errors.csv)")
    p.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    p.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac",
                   help="Preferred tester if both available (default: flac)")
    args = p.parse_args()

    root = os.path.abspath(args.root)
    flacs = list(find_flacs(root))
    total = len(flacs)

    if total == 0:
        print(f"No FLAC files found under: {root}")
        return 0

    if not (has_tool("flac") or has_tool("ffmpeg")):
        print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one and retry.", file=sys.stderr)
        return 2

    print(f"Found {total} FLAC files under: {root}")
    errors: List[Tuple[str, str, str]] = []  # (path, method, message)

    def worker(path: str) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(path, args.prefer)
            return path, ok, method, msg
        except Exception as e:
            return path, False, "exception", repr(e)

    checked = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
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
        out_path = os.path.abspath(args.output)
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

if __name__ == "__main__":
    sys.exit(main())

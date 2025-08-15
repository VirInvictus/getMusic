#!/usr/bin/env python3
"""
Scan subdirectories for FLAC files, verify integrity, and write any failures to CSV.

- Prefers:   flac -t
- Fallback:  ffmpeg -v error -i ... -f null -

Usage:
  python check_flacs.py --root "D:\\Music" --output flac_errors.csv --workers 4
"""

import argparse
import csv
import os
import sys
import subprocess
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

def has_tool(name: str) -> bool:
    return shutil.which(name) is not None

def run(cmd: str) -> (int, str, str):
    # Cross-platform safe subprocess runner
    proc = subprocess.Popen(
        cmd if isinstance(cmd, list) else shlex.split(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    out, err = proc.communicate()
    return proc.returncode, out.strip(), err.strip()

def test_with_flac(filepath: str) -> (bool, str):
    # -t test, -s silent
    code, out, err = run(f'flac -t -s "{filepath}"')
    if code == 0:
        return True, ""
    # Some versions return nonzero and put messages in stderr
    msg = err or out or f"flac exited with code {code}"
    return False, msg

def test_with_ffmpeg(filepath: str) -> (bool, str):
    # Decode test without producing output; errors go to stderr
    # -v error reduces noise; -nostats hides progress
    cmd = f'ffmpeg -v error -nostats -i "{filepath}" -f null -'
    code, out, err = run(cmd)
    if code == 0 and not err:
        return True, ""
    # ffmpeg sometimes exits 0 but prints decode errors; treat any stderr as failure
    if code == 0 and err:
        return False, err
    return False, err or out or f"ffmpeg exited with code {code}"

def test_flac(filepath: str, prefer: str) -> (bool, str, str):
    """
    Returns: (ok, method_used, message_if_error)
    """
    if prefer == "flac":
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", msg if not ok else ""
        elif has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            return ok, "ffmpeg", msg if not ok else ""
        else:
            return False, "none", "Neither 'flac' nor 'ffmpeg' found in PATH."
    else:
        if has_tool("ffmpeg"):
            ok, msg = test_with_ffmpeg(filepath)
            if ok or not has_tool("flac"):
                return ok, "ffmpeg", msg if not ok else ""
        if has_tool("flac"):
            ok, msg = test_with_flac(filepath)
            return ok, "flac", msg if not ok else ""
        return False, "none", "Neither 'flac' nor 'ffmpeg' found in PATH."

def find_flacs(root: str):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".flac"):
                yield os.path.join(dirpath, name)

def main():
    parser = argparse.ArgumentParser(description="Verify FLAC files recursively and report failures.")
    parser.add_argument("--root", default=".", help="Root directory to scan (default: current dir)")
    parser.add_argument("--output", default="flac_errors.csv", help="CSV file to write errors to")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers (default: 4)")
    parser.add_argument("--prefer", choices=["flac", "ffmpeg"], default="flac",
                        help="Preferred tester if both are available (default: flac)")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    flacs = list(find_flacs(root))
    total = len(flacs)
    if total == 0:
        print("No FLAC files found.")
        return 0

    print(f"Found {total} FLAC files under: {root}")
    if not (has_tool("flac") or has_tool("ffmpeg")):
        print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH. Install one to run integrity tests.", file=sys.stderr)
        return 2

    errors = []

    def worker(path: str):
        try:
            ok, method, msg = test_flac(path, args.prefer)
            return (path, ok, method, msg)
        except Exception as e:
            return (path, False, "exception", repr(e))

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(worker, p): p for p in flacs}
        done = 0
        for fut in as_completed(futures):
            done += 1
            path, ok, method, msg = fut.result()
            if not ok:
                errors.append((path, method, msg))
            if done % 50 == 0 or done == total:
                print(f"Progress: {done}/{total} checked...", end="\r", flush=True)

    print()  # newline after progress

    if errors:
        # Write CSV with header
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "method", "error"])
            for row in errors:
                writer.writerow(row)
        print(f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {args.output}")
        # Also echo a short sample to stdout
        for p, method, msg in errors[:5]:
            print(f"- {p} [{method}] -> {msg[:160]}{'...' if len(msg) > 160 else ''}")
        return 1
    else:
        print("✅ All FLAC files passed integrity checks.")
        return 0

if __name__ == "__main__":
    sys.exit(main())

import os
import sys
from collections import defaultdict, Counter
from typing import Dict, List, Tuple

from lattice.utils import is_audio, count_audio_files, _make_pbar
from lattice.tags import get_all_tags, HAVE_MUTAGEN_BASE
from lattice.config import AUDIO_EXTENSIONS, DEFAULT_DUPLICATES_OUTPUT, DEFAULT_TAG_AUDIT_OUTPUT

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

    with open(out_path, "w", encoding="utf-8") as out_file:
        out_file.write("DUPLICATE ALBUM REPORT\n")
        out_file.write(f"Root: {root}\n")
        out_file.write(f"Duplicated albums: {len(duplicates)}  Total directories: {total_dupes}\n")
        out_file.write("=" * 60 + "\n\n")

        for i, ((artist, album), locations) in enumerate(sorted(duplicates.items()), 1):
            out_file.write(f"  {i}. {artist} — {album}\n")
            for directory, formats in locations:
                rel = os.path.relpath(directory, root)
                fmt_str = " ".join(sorted(formats))
                out_file.write(f"     └── {rel}  [{fmt_str}]\n")
            out_file.write("\n")

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

    with open(out_path, "w", encoding="utf-8") as out_file:
        out_file.write("TAG AUDIT REPORT\n")
        out_file.write(f"Root: {root}\n")
        out_file.write(f"Scanned: {total}  Incomplete: {len(issues)}\n")
        if field_counts:
            breakdown = "  ".join(f"{field}: {count}" for field, count in field_counts.most_common())
            out_file.write(f"Breakdown: {breakdown}\n")
        out_file.write("=" * 60 + "\n\n")

        for directory in sorted(by_dir.keys()):
            rel_dir = os.path.relpath(directory, root)
            out_file.write(f"  {rel_dir}/\n")
            for issue in by_dir[directory]:
                filename = os.path.basename(issue["path"])
                out_file.write(f"    {filename}  [{issue['format']}]  missing: {issue['missing']}\n")
            out_file.write("\n")

    if not quiet:
        print(f"\nAudited {total} files. Found {len(issues)} with incomplete tags.")
        print(f"Results written to: {out_path}")
        if field_counts:
            print("  Breakdown:")
            for field, count in field_counts.most_common():
                print(f"    {field}: {count}")

    return 0

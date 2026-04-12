import argparse
import os
import sys
from typing import Optional, List

from lattice.config import (
    VERSION,
    DEFAULT_LIBRARY_OUTPUT,
    DEFAULT_AI_LIBRARY_OUTPUT,
    DEFAULT_FLAC_OUTPUT,
    DEFAULT_MP3_OUTPUT,
    DEFAULT_OPUS_OUTPUT,
    DEFAULT_MISSING_ART_OUTPUT,
    DEFAULT_DUPLICATES_OUTPUT,
    DEFAULT_TAG_AUDIT_OUTPUT,
)

from lattice.modes.library import write_music_library_tree, write_ai_library, write_all_wings
from lattice.modes.integrity import run_flac_mode, run_mp3_mode, run_opus_mode
from lattice.modes.artwork import run_extract_art, run_missing_art
from lattice.modes.audit import run_duplicates, run_tag_audit
from lattice.modes.stats import run_stats
from lattice.tui import interactive_menu
from lattice.utils import _reset_terminal

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice",
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

    p.add_argument("--root", default=None, help="Root directory (default: read from config or current dir)")
    p.add_argument("pos_root", nargs="?", default=None, help="Root directory (positional fallback)")
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

def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        args = build_parser().parse_args(argv)
        
        raw_root = args.pos_root if args.pos_root is not None else args.root

        if raw_root is None:
            from lattice.config import get_library_root, set_library_root
            config_root = get_library_root()
            if config_root and os.path.exists(config_root):
                root = config_root
            else:
                if sys.stdin.isatty():
                    print("First run: No library root configured.")
                    raw_input_root = input("Enter path to your music library (or press Enter for current directory): ").strip()
                    if raw_input_root:
                        root = os.path.abspath(os.path.expanduser(raw_input_root))
                        set_library_root(root)
                        print(f"Library root saved to {root}")
                    else:
                        root = os.path.abspath(".")
                else:
                    root = os.path.abspath(".")
        else:
            root = os.path.abspath(os.path.expanduser(raw_root))

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
            run_stats(root, args.output, quiet=args.quiet)
            return 0

        build_parser().print_help()
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130

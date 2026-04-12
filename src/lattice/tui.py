import os
import sys
from typing import Dict, Any, Optional, List

try:
    import curses
    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False

from lattice.utils import _reset_terminal
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
from lattice.modes.stats import run_stats
from lattice.modes.integrity import run_flac_mode, run_mp3_mode, run_opus_mode
from lattice.modes.artwork import run_extract_art, run_missing_art
from lattice.modes.audit import run_duplicates, run_tag_audit

# =====================================
# Curses TUI / Fallbacks
# =====================================

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()

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
    """Full-screen arrow-key menu using curses."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    flat: list[tuple[int, int]] = []
    for si, (_, items) in enumerate(sections):
        for ii in range(len(items)):
            flat.append((si, ii))

    def _draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        box_h = 3
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, f" {title:^{INNER - 2}} ",
              curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u2560" + "\u2550" * INNER + "\u2563", fa)
        y += 1

        idx = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
                y += 1

            if hdr:
                content = f"  {hdr}" + " " * (INNER - len(hdr) - 2)
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, content,
                      curses.color_pair(_CP_HEADER) | curses.A_BOLD)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1

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

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        y += 2

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
                pass

    try:
        return curses.wrapper(_run)
    except curses.error:
        return None

def _tui_prompt_str(label: str, default: Optional[str]) -> str:
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

            _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
            y += 1

            lbl = f"  {label}"
            padded_lbl = lbl + " " * max(0, INNER - len(lbl))
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, padded_lbl[:INNER],
                         curses.color_pair(_CP_HEADER) | curses.A_BOLD)
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1

            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1

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

            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2

            hints = "\u23ce Confirm  Esc Default"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(stdscr, y, hx, hints,
                         curses.color_pair(_CP_HINT) | curses.A_DIM)

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
            elif key == 27:
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
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(130)
        return raw or (default or "")

def _tui_pause() -> None:
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
    "s": (4, 0), "settings": (4, 0), "config": (4, 0), "c": (4, 0),
    "q": None, "quit": None, "exit": None,
}

_LIB_FALLBACK_MAP: Dict[str, Optional[tuple]] = {
    "1": (0, 0), "tree": (0, 0), "lib": (0, 0),
    "2": (0, 1), "ai": (0, 1),
    "3": (0, 2), "wings": (0, 2),
    "b": None, "back": None, "": None,
}

def _fallback_input(prompt: str, mapping: dict) -> Any:
    try:
        ch = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return mapping.get(ch, "invalid")

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

def _select_main() -> Optional[tuple]:
    if _USE_CURSES:
        return _tui_select(f"Lattice v{VERSION}", _MAIN_SECTIONS)
    _box_menu(f"Lattice v{VERSION}", [
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

def _tui_page(title: str, content: str) -> None:
    if not _USE_CURSES:
        print(content)
        _pause()
        return

    def _run(stdscr):
        _init_tui_colors()
        curses.curs_set(0)
        lines = content.replace('\x00', '').split('\n')
        offset = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            
            box_w = min(80, w - 2)
            inner = box_w - 2
            bx = max(0, (w - box_w) // 2)
            
            max_lines = max(5, h - 6)
            visible_lines = lines[offset:offset+max_lines]
            
            y = max(0, (h - (len(visible_lines) + 5)) // 2)
            
            _safe_addstr(stdscr, y, bx, "╔" + "═" * inner + "╗", curses.color_pair(_CP_FRAME))
            y += 1
            _safe_addstr(stdscr, y, bx, "║", curses.color_pair(_CP_FRAME))
            _safe_addstr(stdscr, y, bx+1, f" {title}".ljust(inner), curses.color_pair(_CP_TITLE) | curses.A_BOLD)
            _safe_addstr(stdscr, y, bx+box_w-1, "║", curses.color_pair(_CP_FRAME))
            y += 1
            _safe_addstr(stdscr, y, bx, "╠" + "═" * inner + "╣", curses.color_pair(_CP_FRAME))
            y += 1
            
            for line in visible_lines:
                _safe_addstr(stdscr, y, bx, "║", curses.color_pair(_CP_FRAME))
                _safe_addstr(stdscr, y, bx+1, (" " + line)[:inner].ljust(inner), curses.color_pair(_CP_ITEM))
                _safe_addstr(stdscr, y, bx+box_w-1, "║", curses.color_pair(_CP_FRAME))
                y += 1
                
            _safe_addstr(stdscr, y, bx, "╚" + "═" * inner + "╝", curses.color_pair(_CP_FRAME))
            y += 2
            
            hints = "↑↓ Scroll  q/Esc Close"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)
            
            stdscr.refresh()
            
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')) and offset > 0:
                offset -= 1
            elif key in (curses.KEY_DOWN, ord('j')) and offset < max(0, len(lines) - max_lines):
                offset += 1
            elif key in (ord('q'), ord('Q'), 27):
                break
            elif key == curses.KEY_RESIZE:
                pass
                
    try:
        curses.wrapper(_run)
    except curses.error:
        print(content)
        _pause()

def _library_submenu(root: str) -> None:
    while True:
        result = _select_library()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (1, 0):
            return

        _reset_terminal()

        if result == (0, 0):
            output = _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT) or DEFAULT_LIBRARY_OUTPUT
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith('y')
            write_music_library_tree(root, output, quiet=False, show_genre=show_g)
            print(f"\n  Library written to {output}")
            _pause()

        elif result == (0, 1):
            output = _prompt_str("Output file", DEFAULT_AI_LIBRARY_OUTPUT) or DEFAULT_AI_LIBRARY_OUTPUT
            write_ai_library(root, output, quiet=False)
            _pause()

        elif result == (0, 2):
            outdir = _prompt_str("Output directory", "wings") or "wings"
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith('y')
            show_p = _prompt_str("Include paths? (y/N)", "N").lower().startswith('y')
            write_all_wings(root, outdir, quiet=False, show_genre=show_g, show_paths=show_p)
            _pause()

def interactive_menu() -> int:
    import lattice.utils as utils
    from lattice.config import get_library_root, set_library_root
    utils.IN_TUI = True

    while True:
        root = get_library_root()
        if not root or not os.path.exists(root):
            root = _prompt_path("First run: Enter path to your music library")
            set_library_root(root)
            continue
            
        _reset_terminal()
        result = _select_main()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (5, 0):  # Quit (was 4, 0)
            return 0

        if result == (4, 0):  # Change root
            new_root = _prompt_path(f"Change library root (current: {root})")
            set_library_root(new_root)
            continue

        if result == (0, 0):
            _library_submenu(root)

        elif result == (0, 1):
            output = _prompt_str("Output file (leave blank for screen)", "").strip() or None
            report = run_stats(root, output, quiet=False)
            if not output and report:
                _tui_page("Library Statistics", report)
            else:
                _pause()

        elif result == (1, 0):
            output = _prompt_str("Output file", DEFAULT_FLAC_OUTPUT) or DEFAULT_FLAC_OUTPUT
            workers = _prompt_int("Workers", 4)
            pref = _prompt_str("Preferred tool (flac/ffmpeg)", "flac").lower()
            run_flac_mode(root, output, workers, pref, quiet=False)
            _pause()

        elif result == (1, 1):
            output = _prompt_str("Output file", DEFAULT_MP3_OUTPUT) or DEFAULT_MP3_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_mp3_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )
            _pause()

        elif result == (1, 2):
            output = _prompt_str("Output file", DEFAULT_OPUS_OUTPUT) or DEFAULT_OPUS_OUTPUT
            workers = _prompt_int("Workers", 4)
            include_ok = _prompt_str("Include OK rows? (y/N)", "N").lower().startswith('y')
            run_opus_mode(
                root, output, workers, None,
                only_errors=not include_ok, verbose=include_ok, quiet=False,
            )
            _pause()

        elif result == (2, 0):
            dry = _prompt_str("Dry run? (y/N)", "N").lower().startswith('y')
            run_extract_art(root, quiet=False, dry_run=dry)
            _pause()

        elif result == (2, 1):
            output = _prompt_str("Output file", DEFAULT_MISSING_ART_OUTPUT) or DEFAULT_MISSING_ART_OUTPUT
            run_missing_art(root, output, quiet=False)
            _pause()

        elif result == (3, 0):
            output = _prompt_str("Output file", DEFAULT_DUPLICATES_OUTPUT) or DEFAULT_DUPLICATES_OUTPUT
            run_duplicates(root, output, quiet=False)
            _pause()

        elif result == (3, 1):
            output = _prompt_str("Output file", DEFAULT_TAG_AUDIT_OUTPUT) or DEFAULT_TAG_AUDIT_OUTPUT
            run_tag_audit(root, output, quiet=False)
            _pause()

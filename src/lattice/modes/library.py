import os
import sys
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from lattice.utils import count_audio_files, _make_pbar, is_audio, clean_song_name, format_rating
from lattice.tags import get_all_tags

# =====================================
# Mode: Library tree
# =====================================

def write_music_library_tree(root_dir: str, output_file: str, *, quiet: bool = False, show_genre: bool = False) -> None:
    root_dir = os.path.abspath(root_dir)
    total_files = count_audio_files(root_dir)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {root_dir}\n")

    pbar = _make_pbar(total_files, "Scanning library", quiet)

    output_file = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

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
                        if is_audio(s)
                    ])

                    if not songs:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        f.write("      └── [No Audio Files Found]\n")
                        continue

                    # Genre header: defer until first song read, or write immediately
                    if show_genre:
                        album_header_written = False
                    else:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        album_header_written = True

                    for j, song in enumerate(songs):
                        pbar.update(1)

                        song_path = os.path.join(album_path, song)
                        t = get_all_tags(song_path)

                        # Write album header with genre from first track
                        if not album_header_written:
                            genre_str = f" ({t.genre})" if t.genre else ""
                            f.write(f"  {connector} ALBUM: {album}{genre_str}\n")
                            album_header_written = True

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
        pbar.close()

# =====================================
# Mode: AI-readable library export
# =====================================

def write_ai_library(root_dir: str, output_file: str, *, quiet: bool = False) -> None:
    """Write a flat, token-efficient library summary for LLM consumption.

    One line per album: Artist | Album | Genre | Rating | Tracks
    Rating is the average of all rated tracks in the album, or blank if unrated.
    Tracks is the number of audio files surviving in the album directory.
    Genre is sampled from the first track with a genre tag.
    """
    root_dir = os.path.abspath(root_dir)
    total = count_audio_files(root_dir)

    if not quiet:
        print(f"Scanning {total} files under: {root_dir}")

    pbar = _make_pbar(total, "Building AI library", quiet)

    # (artist, album, genre, rating, track_count)
    albums: List[Tuple[str, str, str, str, int]] = []

    for artist_dir in sorted(os.listdir(root_dir)):
        artist_path = os.path.join(root_dir, artist_dir)
        if not os.path.isdir(artist_path):
            continue

        for album_name in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album_name)
            if not os.path.isdir(album_path):
                continue

            songs = [
                s for s in os.listdir(album_path)
                if is_audio(s)
            ]
            if not songs:
                continue

            # Scan all tracks for ratings; sample first for genre
            album_genre = ""
            album_artist = artist_dir
            ratings: List[float] = []

            for song in songs:
                song_path = os.path.join(album_path, song)
                t = get_all_tags(song_path)

                if not album_genre and t.genre:
                    album_genre = t.genre
                if t.rating is not None:
                    ratings.append(t.rating)

                pbar.update(1)

            # Average rating, rounded to one decimal
            if ratings:
                avg = sum(ratings) / len(ratings)
                rating_str = f"{avg:.1f}"
            else:
                rating_str = ""

            albums.append((album_artist, album_name, album_genre, rating_str, len(songs)))

    pbar.close()

    out_path = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("Artist | Album | Genre | Rating | Tracks\n")
        f.write("-" * 50 + "\n")
        for artist, album, genre, rating, tracks in albums:
            f.write(f"{artist} | {album} | {genre} | {rating} | {tracks}\n")

    if not quiet:
        rated = sum(1 for _, _, _, r, _ in albums if r)
        print(f"\nWrote {len(albums)} albums ({rated} rated) to: {out_path}")

# =====================================
# Mode: All wings (genre-based library files)
# =====================================

def _scan_genres(root_dir: str, quiet: bool = False) -> Dict[str, List[Tuple[str, str]]]:
    """Scan the library and group (artist_dir, album_dir) pairs by genre.

    Returns a dict mapping genre name to a sorted list of (artist_dir, album_dir).
    Albums whose tracks have no genre tag are collected under "Uncategorized".
    """
    total = count_audio_files(root_dir)
    if not quiet:
        print(f"Scanning {total} files for genre tags...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    # genre -> set of (artist_dir, album_dir)
    genre_map: Dict[str, set] = defaultdict(set)

    for artist_dir in sorted(os.listdir(root_dir)):
        artist_path = os.path.join(root_dir, artist_dir)
        if not os.path.isdir(artist_path):
            continue

        for album_dir in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album_dir)
            if not os.path.isdir(album_path):
                continue

            songs = [s for s in os.listdir(album_path) if is_audio(s)]
            album_genre = ""
            for song in songs:
                t = get_all_tags(os.path.join(album_path, song))
                pbar.update(1)
                if not album_genre and t.genre:
                    album_genre = t.genre

            genre_map[album_genre or "Uncategorized"].add((artist_dir, album_dir))

    pbar.close()

    # Convert sets to sorted lists
    return {g: sorted(pairs) for g, pairs in genre_map.items()}

def write_all_wings(root_dir: str, outdir: str, *, quiet: bool = False,
                    show_genre: bool = False, show_paths: bool = False) -> int:
    """Generate a separate library tree file for each genre.

    Scans the entire library (root/Artist/Album/songs) to determine each
    album's genre from its tags, then writes one text file per genre into
    *outdir* — analogous to virtual-library wings in Calibre.
    """
    root_dir = os.path.abspath(root_dir)
    genre_groups = _scan_genres(root_dir, quiet=quiet)

    if not genre_groups:
        print("No albums found under root.", file=sys.stderr)
        return 1

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(genre_groups)} genres. Writing wings...\n")

    for genre_name in sorted(genre_groups):
        pairs = genre_groups[genre_name]
        safe_name = re.sub(r'[^\w\s-]', '', genre_name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_Library.txt")

        if not quiet:
            print(f"→ {genre_name} ({len(pairs)} albums)")

        with open(output, 'w', encoding='utf-8') as f:
            # Group albums by artist
            artist_albums: Dict[str, List[str]] = defaultdict(list)
            for artist_dir, album_dir in pairs:
                artist_albums[artist_dir].append(album_dir)

            for artist_dir in sorted(artist_albums):
                f.write(f"ARTIST: {artist_dir}\n")
                albums = sorted(artist_albums[artist_dir])

                for i, album in enumerate(albums):
                    album_path = os.path.join(root_dir, artist_dir, album)
                    connector = "└──" if i == len(albums) - 1 else "├──"

                    songs = sorted([s for s in os.listdir(album_path) if is_audio(s)])

                    if not songs:
                        f.write(f"  {connector} ALBUM: {album}\n")
                        f.write("      └── [No Audio Files Found]\n")
                        continue

                    genre_str = ""
                    if show_genre:
                        first_tag = get_all_tags(os.path.join(album_path, songs[0]))
                        if first_tag.genre:
                            genre_str = f" ({first_tag.genre})"
                    
                    path_str = f" [{album_path}]" if show_paths else ""
                    f.write(f"  {connector} ALBUM: {album}{genre_str}{path_str}\n")

                    for j, song in enumerate(songs):
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

    if not quiet:
        total_albums = sum(len(p) for p in genre_groups.values())
        print(f"\n{len(genre_groups)} wings ({total_albums} albums) written to: {outdir}")
    return 0

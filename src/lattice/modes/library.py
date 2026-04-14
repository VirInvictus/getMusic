import os
import sys
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from lattice.utils import count_audio_files, _make_pbar, is_audio, clean_song_name, format_rating, parse_layout
from lattice.tags import get_all_tags, TagBundle

# =====================================
# Mode: Library tree
# =====================================

def write_music_library_tree(root_dir: str, output_file: str, *, layout: str = "{artist}/{album}", quiet: bool = False, show_genre: bool = False) -> None:
    root_dir = os.path.abspath(root_dir)
    total_files = count_audio_files(root_dir)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {root_dir}\n")

    pbar = _make_pbar(total_files, "Scanning library", quiet)

    # directory -> {artist: str, album: str, songs: list}
    albums_by_dir: Dict[str, Dict] = {}

    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        audio_in_dir = [f for f in files if is_audio(f)]
        if not audio_in_dir:
            continue
            
        artists_count: Dict[str, int] = defaultdict(int)
        albums_count: Dict[str, int] = defaultdict(int)
        songs = []
        
        for f in audio_in_dir:
            filepath = os.path.join(dirpath, f)
            rel_path = os.path.relpath(filepath, root_dir)
            parsed = parse_layout(rel_path, layout)
            t = get_all_tags(filepath)
            
            artist = t.artist or parsed.get("artist", "Unknown Artist")
            album = t.album or parsed.get("album", "Unknown Album")
            
            artists_count[artist] += 1
            albums_count[album] += 1
            songs.append((f, filepath, t))
            pbar.update(1)
            
        best_artist = max(artists_count, key=artists_count.get) if artists_count else "Unknown Artist"
        best_album = max(albums_count, key=albums_count.get) if albums_count else "Unknown Album"
        
        albums_by_dir[dirpath] = {
            "artist": best_artist,
            "album": best_album,
            "songs": songs
        }

    pbar.close()

    # Re-group for display: artist -> album_name -> list of songs
    # We still want to group same-artist albums together in the output
    tree: Dict[str, Dict[str, List[Tuple[str, str, TagBundle]]]] = defaultdict(lambda: defaultdict(list))
    for data in albums_by_dir.values():
        tree[data["artist"]][data["album"]].extend(data["songs"])

    output_file = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            for artist in sorted(tree.keys()):
                f.write(f"ARTIST: {artist}\n")
                albums = sorted(tree[artist].keys())

                for i, album in enumerate(albums):
                    songs = sorted(tree[artist][album], key=lambda x: x[0])
                    connector = "└──" if i == len(albums) - 1 else "├──"

                    genre_str = ""
                    if show_genre and songs:
                        first_tag = songs[0][2]
                        if first_tag.genre:
                            genre_str = f" ({first_tag.genre})"

                    f.write(f"  {connector} ALBUM: {album}{genre_str}\n")

                    for j, (song, song_path, t) in enumerate(songs):
                        if t.title or t.artist:
                            parts: List[str] = []
                            if t.trackno:
                                parts.append(f"{int(t.trackno):02d}.")
                            if t.artist and t.artist != artist:
                                parts.append(t.artist)
                            if t.title:
                                if t.artist and t.artist != artist:
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

# =====================================
# Mode: AI-readable library export
# =====================================

def write_ai_library(root_dir: str, output_file: str, *, layout: str = "{artist}/{album}", quiet: bool = False) -> None:
    """Write a flat, token-efficient library summary for LLM consumption."""
    root_dir = os.path.abspath(root_dir)
    total = count_audio_files(root_dir)

    if not quiet:
        print(f"Scanning {total} files under: {root_dir}")

    pbar = _make_pbar(total, "Building AI library", quiet)

    # directory -> {artist: str, album: str, genre: str, songs: list}
    albums_by_dir: Dict[str, Dict] = {}

    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        audio_in_dir = [f for f in files if is_audio(f)]
        if not audio_in_dir:
            continue
            
        artists_count: Dict[str, int] = defaultdict(int)
        albums_count: Dict[str, int] = defaultdict(int)
        genres_count: Dict[str, int] = defaultdict(int)
        songs = []

        for f in audio_in_dir:
            filepath = os.path.join(dirpath, f)
            rel_path = os.path.relpath(filepath, root_dir)
            parsed = parse_layout(rel_path, layout)
            t = get_all_tags(filepath)
            artist = t.artist or parsed.get("artist", "Unknown Artist")
            album = t.album or parsed.get("album", "Unknown Album")
            
            artists_count[artist] += 1
            albums_count[album] += 1
            if t.genre:
                genres_count[t.genre] += 1
            songs.append((f, filepath, t))
            pbar.update(1)
            
        best_artist = max(artists_count, key=artists_count.get) if artists_count else "Unknown Artist"
        best_album = max(albums_count, key=albums_count.get) if albums_count else "Unknown Album"
        best_genre = max(genres_count, key=genres_count.get) if genres_count else ""
        
        albums_by_dir[dirpath] = {
            "artist": best_artist,
            "album": best_album,
            "genre": best_genre,
            "songs": songs
        }

    pbar.close()

    albums: List[Tuple[str, str, str, str, int]] = []
    for data in albums_by_dir.values():
        artist = data["artist"]
        album = data["album"]
        songs = data["songs"]
        album_genre = data["genre"]
        
        ratings: List[float] = []
        for song_f, song_path, t in songs:
            if t.rating is not None:
                ratings.append(t.rating)
                
        if ratings:
            avg = sum(ratings) / len(ratings)
            rating_str = f"{avg:.1f}"
        else:
            rating_str = ""

        albums.append((artist, album, album_genre, rating_str, len(songs)))

    # Sort final albums list by artist then album
    albums.sort(key=lambda x: (x[0].lower(), x[1].lower()))

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

def write_all_wings(root_dir: str, outdir: str, *, layout: str = "{artist}/{album}", quiet: bool = False,
                    show_genre: bool = False, show_paths: bool = False) -> int:
    """Generate a separate library tree file for each genre."""
    root_dir = os.path.abspath(root_dir)
    total = count_audio_files(root_dir)
    if not quiet:
        print(f"Scanning {total} files for genre tags...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    
    # directory -> {artist: str, album: str, genre: str, songs: list}
    albums_by_dir: Dict[str, Dict] = {}

    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        audio_in_dir = [f for f in files if is_audio(f)]
        if not audio_in_dir:
            continue
            
        artists_count: Dict[str, int] = defaultdict(int)
        albums_count: Dict[str, int] = defaultdict(int)
        genres_count: Dict[str, int] = defaultdict(int)
        songs = []
        
        for f in audio_in_dir:
            filepath = os.path.join(dirpath, f)
            rel_path = os.path.relpath(filepath, root_dir)
            parsed = parse_layout(rel_path, layout)
            t = get_all_tags(filepath)
            artist = t.artist or parsed.get("artist", "Unknown Artist")
            album = t.album or parsed.get("album", "Unknown Album")
            
            artists_count[artist] += 1
            albums_count[album] += 1
            if t.genre:
                genres_count[t.genre] += 1
            songs.append((f, filepath, t))
            pbar.update(1)

        best_artist = max(artists_count, key=artists_count.get) if artists_count else "Unknown Artist"
        best_album = max(albums_count, key=albums_count.get) if albums_count else "Unknown Album"
        best_genre = max(genres_count, key=genres_count.get) if genres_count else "Uncategorized"

        albums_by_dir[dirpath] = {
            "artist": best_artist,
            "album": best_album,
            "genre": best_genre,
            "songs": songs
        }

    pbar.close()
    
    if not albums_by_dir:
        print("No albums found under root.", file=sys.stderr)
        return 1
        
    # Re-bucket by genre -> artist -> album
    final_wings: Dict[str, Dict[str, Dict[str, List[Tuple[str, str, TagBundle]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    album_paths: Dict[Tuple[str, str], str] = {}
    
    for dirpath, data in albums_by_dir.items():
        genre_str = data["genre"]
        artist = data["artist"]
        album = data["album"]
        songs = data["songs"]
        
        for genre in (g.strip() for g in genre_str.split('/') if g.strip()):
            final_wings[genre][artist][album].extend(songs)
        album_paths[(artist, album)] = dirpath

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(final_wings)} genres. Writing wings...\n")

    for genre_name in sorted(final_wings):
        artist_albums = final_wings[genre_name]
        safe_name = re.sub(r'[^\w\s-]', '_', genre_name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_Library.txt")
        album_count = sum(len(albums) for albums in artist_albums.values())

        if not quiet:
            print(f"→ {genre_name} ({album_count} albums)")

        with open(output, 'w', encoding='utf-8') as f:
            for artist in sorted(artist_albums):
                f.write(f"ARTIST: {artist}\n")
                albums = sorted(artist_albums[artist])

                for i, album in enumerate(albums):
                    songs = sorted(artist_albums[artist][album], key=lambda x: x[0])
                    connector = "└──" if i == len(albums) - 1 else "├──"

                    genre_str = ""
                    if show_genre and songs:
                        first_tag = songs[0][2]
                        if first_tag.genre:
                            genre_str = f" ({first_tag.genre})"
                    
                    album_path = album_paths.get((artist, album), "")
                    path_str = f" [{album_path}]" if show_paths and album_path else ""
                    f.write(f"  {connector} ALBUM: {album}{genre_str}{path_str}\n")

                    for j, (song, song_path, t) in enumerate(songs):
                        if t.title or t.artist:
                            parts: List[str] = []
                            if t.trackno:
                                parts.append(f"{int(t.trackno):02d}.")
                            if t.artist and t.artist != artist:
                                parts.append(t.artist)
                            if t.title:
                                if t.artist and t.artist != artist:
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
        total_albums = sum(sum(len(albums) for albums in artist_albums.values()) for artist_albums in final_wings.values())
        print(f"\n{len(final_wings)} wings ({total_albums} albums) written to: {outdir}")
    return 0

# =====================================
# Mode: AI wings (per-genre flat files)
# =====================================

def write_ai_wings(root_dir: str, outdir: str, *, layout: str = "{artist}/{album}", quiet: bool = False) -> int:
    """Generate separate, token-efficient AI library files for each genre."""
    root_dir = os.path.abspath(root_dir)
    total = count_audio_files(root_dir)
    if not quiet:
        print(f"Scanning {total} files for AI wings...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    
    # genre -> list of (artist, album, genre, path)
    wings: Dict[str, List[Tuple[str, str, str, str]]] = defaultdict(list)
    
    # directory -> {artists: {name: count}, albums: {name: count}, genres: {name: count}}
    album_data: Dict[str, Dict] = defaultdict(lambda: {
        "artists": defaultdict(int),
        "albums": defaultdict(int),
        "genres": defaultdict(int)
    })

    for dirpath, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if is_audio(f):
                filepath = os.path.join(dirpath, f)
                rel_path = os.path.relpath(filepath, root_dir)
                parsed = parse_layout(rel_path, layout)
                t = get_all_tags(filepath)
                artist = t.artist or parsed.get("artist", "Unknown Artist")
                album = t.album or parsed.get("album", "Unknown Album")
                
                album_data[dirpath]["artists"][artist] += 1
                album_data[dirpath]["albums"][album] += 1
                if t.genre:
                    album_data[dirpath]["genres"][t.genre] += 1
                pbar.update(1)

    pbar.close()
    
    if not album_data:
        print("No albums found under root.", file=sys.stderr)
        return 1

    # Finalize data for each directory
    for dirpath, data in album_data.items():
        # Pick most frequent artist, album, and genre for this directory
        best_artist = max(data["artists"], key=data["artists"].get) if data["artists"] else "Unknown Artist"
        best_album = max(data["albums"], key=data["albums"].get) if data["albums"] else "Unknown Album"
        
        if data["genres"]:
            album_genre_str = max(data["genres"], key=data["genres"].get)
        else:
            album_genre_str = "Uncategorized"
        
        for album_genre in (g.strip() for g in album_genre_str.split('/') if g.strip()):
            wings[album_genre].append((best_artist, best_album, album_genre, dirpath))

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(wings)} genres. Writing AI wings...\n")

    for genre_name in sorted(wings):
        albums = sorted(wings[genre_name])
        safe_name = re.sub(r'[^\w\s-]', '_', genre_name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_AI.txt")

        if not quiet:
            print(f"→ {genre_name} ({len(albums)} albums)")

        with open(output, 'w', encoding='utf-8') as f:
            f.write("Artist | Album | Genre | Location\n")
            f.write("-" * 60 + "\n")
            for artist, album, genre, path in albums:
                f.write(f"{artist} | {album} | {genre} | {path}\n")

    if not quiet:
        total_albums = sum(len(a) for a in wings.values())
        print(f"\n{len(wings)} AI wings ({total_albums} albums) written to: {outdir}")
    return 0

import os
import re
import sys
from mutagen import File
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, POPM
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
from mutagen.asf import ASF

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma', '.aac'}


# ---------- Utility Functions ----------

def clean_song_name(filename):
    """Extract track number and song title from filename without double numbering."""
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


def normalize_rating(val):
    """Convert any numeric rating scale to 0–5 stars."""
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


def format_rating(rating):
    """Return formatted stars for a rating."""
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


def update_progress(current, total, prefix="Progress"):
    """Display progress bar."""
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


def count_audio_files(root_dir):
    """Count total audio files in the library."""
    total = 0
    for _, _, files in os.walk(root_dir):
        total += sum(1 for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS)
    return total


# ---------- Tag Helpers ----------

def _first_text(val):
    """
    Normalize a mutagen tag value to a plain string if possible.
    Handles lists/tuples and ASF objects.
    """
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    # ASF values sometimes have .value
    try:
        if hasattr(val, "value"):
            val = val.value
    except Exception:
        pass
    if val is None:
        return None
    return str(val).strip() or None


def _parse_track_number(val):
    """
    Parse track number from various formats:
    - "3" or "03" or "3/12"
    - MP4 'trkn': [(track, total)]
    """
    if val is None:
        return None

    # MP4 trkn -> list of tuples
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


def get_title_artist_track(file_path):
    """
    Try hard to get (title, artist, trackno) from tags across formats.
    Falls back progressively and returns (None, None, None) if not found.
    """
    title = artist = None
    trackno = None

    # First pass: 'easy' mutagen (unified keys for many formats)
    try:
        easy = File(file_path, easy=True)
        if easy and easy.tags:
            title = _first_text(easy.tags.get('title'))
            artist = _first_text(easy.tags.get('artist')) or _first_text(easy.tags.get('albumartist'))
            trackno = _parse_track_number(easy.tags.get('tracknumber'))
    except Exception:
        pass

    # If still missing, do format-specific
    try:
        audio = File(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.mp3':
            try:
                id3 = ID3(file_path)
                if title is None and id3.get('TIT2'):
                    title = _first_text(id3.get('TIT2').text)
                if artist is None:
                    # Prefer TPE1 (track artist), then TPE2 (band/album artist)
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
            # keys can vary in case; iterate
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
            # common ASF names
            # 'Title', 'Author', 'WM/AlbumArtist', 'WM/TrackNumber'
            name_map = {}
            for k in tags.keys():
                name_map[k.lower()] = k
            if title is None:
                k = name_map.get('title')
                if k:
                    title = _first_text(tags.get(k))
            if artist is None:
                k = name_map.get('author') or name_map.get('wm/albumartist')
                if k:
                    artist = _first_text(tags.get(k))
            if trackno is None:
                k = name_map.get('wm/tracknumber') or name_map.get('tracknumber')
                if k:
                    trackno = _parse_track_number(tags.get(k))

    except Exception:
        pass

    return title, artist, trackno


# ---------- Metadata Extraction ----------

def get_rating(file_path):
    """Get normalized rating from audio file, prioritizing WMP POPM for MP3."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        audio = File(file_path)
        if not audio:
            return None

        # --- MP3 ---
        if ext == '.mp3':
            try:
                id3 = ID3(file_path)
            except ID3NoHeaderError:
                return None

            # POPM frames — prefer Windows Media Player 9 Series
            for popm in id3.getall('POPM'):
                if popm.email == 'Windows Media Player 9 Series':
                    wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                    return wmp_map.get(popm.rating, normalize_rating(popm.rating))

            # If no WMP POPM, fall back to other POPM
            for popm in id3.getall('POPM'):
                if popm.rating > 0:
                    return normalize_rating(popm.rating)

            # TXXX frames as last resort
            for txxx in id3.getall('TXXX'):
                desc = (txxx.desc or "").lower()
                if 'rating' in desc or desc in ('rate', 'score', 'stars'):
                    val = txxx.text[0] if txxx.text else None
                    if val and str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        # --- FLAC / OGG ---
        elif isinstance(audio, (FLAC, OggVorbis)):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower() or 'score' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        # --- MP4 / M4A ---
        elif isinstance(audio, MP4):
            for key, val in (audio.tags or {}).items():
                k = key.lower() if isinstance(key, str) else str(key).lower()
                if 'rate' in k or 'rating' in k:
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        # --- WMA / ASF ---
        elif isinstance(audio, ASF):
            for key, val in (audio.tags or {}).items():
                if 'rating' in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if str(val).replace('.', '').isdigit():
                        return normalize_rating(val)

        return None

    except Exception:
        return None


# ---------- Main Logic ----------

def write_music_library_tree(root_dir, output_file):
    print("Counting audio files.")
    total_files = count_audio_files(root_dir)
    print(f"Found {total_files} audio files to process\n")

    current_file = 0
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

                    # Build display string: "03. Artist — Title"
                    if title or artist_tag:
                        parts = []
                        if trackno:
                            parts.append(f"{int(trackno):02d}.")
                        if artist_tag:
                            parts.append(artist_tag)
                        if title:
                            # en dash between artist and title if both exist
                            if artist_tag:
                                parts.append("—")
                            parts.append(title)
                        display_name = " ".join(parts).strip()
                    else:
                        # Fallback to filename parsing if tags are missing
                        display_name = clean_song_name(song)

                    ext = os.path.splitext(song)[1].lower().strip('.')
                    rating = get_rating(song_path)
                    rating_str = format_rating(rating)

                    song_connector = "└──" if j == len(songs) - 1 else "├──"
                    f.write(f"      {song_connector} SONG: {display_name} ({ext}){rating_str}\n")
            f.write("\n")


if __name__ == "__main__":
    current_directory = os.getcwd()
    output_filename = "music_library.txt"

    print(f"Scanning music library in: {current_directory}")
    write_music_library_tree(current_directory, output_filename)
    print(f"\nMusic library written to {output_filename}")

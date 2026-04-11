import re

VERSION = "4.0.1"

DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.txt"
DEFAULT_MP3_OUTPUT = "mp3_scan_results.txt"
DEFAULT_OPUS_OUTPUT = "opus_scan_results.txt"
DEFAULT_MISSING_ART_OUTPUT = "missing_art.txt"
DEFAULT_DUPLICATES_OUTPUT = "duplicates.txt"
DEFAULT_TAG_AUDIT_OUTPUT = "tag_audit.txt"
DEFAULT_AI_LIBRARY_OUTPUT = "library_ai.txt"
DEFAULT_STATS_OUTPUT = "library_stats.txt"

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.wav', '.wma', '.aac'}

COVER_NAMES = {"cover.jpg", "cover.jpeg", "cover.png",
               "folder.jpg", "folder.jpeg", "folder.png",
               "front.jpg", "front.jpeg", "front.png",
               "album.jpg", "album.jpeg", "album.png"}

ART_FORMAT_PRIORITY = ['.flac', '.opus', '.ogg', '.m4a', '.mp3']

RE_CLEAN_PREFIX = re.compile(r'^[^\-\d]*-\s*')
RE_CLEAN_PATTERNS = [
    re.compile(r'^(?:\d+\s*[-–—]\s*)?(\d+)\.?\s*[-–—]?\s*(.+)$'),
    re.compile(r'^[Tt]rack\s*(\d+)\.?\s*[-–—]?\s*(.+)$'),
    re.compile(r'^(\d+)\s+(.+)$')
]

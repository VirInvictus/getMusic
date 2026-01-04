#!/usr/bin/env python3
#
#

import os
import base64
from mutagen import File
from mutagen.flac import Picture

# Configuration
MUSIC_DIR = os.path.expanduser("~/Music")
COVER_NAMES = ["cover.jpg", "folder.jpg", "front.jpg", "album.jpg"]

def get_image_from_opus(filepath):
    """
    Extracts binary image data from an Opus file's Vorbis comment.
    Opus wraps art in a FLAC Picture block, base64-encoded inside the tag.
    """
    try:
        audio = File(filepath)
        if audio is None or audio.tags is None:
            return None

        # Opus stores art in the 'METADATA_BLOCK_PICTURE' tag
        b64_data = audio.tags.get("METADATA_BLOCK_PICTURE")
        if not b64_data:
            return None

        # There might be multiple images; take the first one
        for b64_entry in b64_data:
            try:
                # 1. Decode Base64 wrapper
                data = base64.b64decode(b64_entry)

                # 2. Parse the FLAC Picture Block structure
                # We use Mutagen's helper to strip the headers (MIME type, dims, etc.)
                picture = Picture(data)
                return picture.data
            except Exception as e:
                print(f"  [!] Error parsing internal art in {filepath}: {e}")
                continue

    except Exception as e:
        print(f"  [!] Error reading file {filepath}: {e}")

    return None

def process_directory(directory):
    # 1. Check if art already exists
    for name in COVER_NAMES:
        if os.path.exists(os.path.join(directory, name)):
            # print(f"  [i] Skipping {directory} (Art exists)")
            return

    # 2. Find the first Opus file
    opus_file = None
    for file in os.listdir(directory):
        if file.lower().endswith(".opus"):
            opus_file = os.path.join(directory, file)
            break

    if not opus_file:
        return

    # 3. Extract and Write
    print(f"[+] Processing: {directory}")
    image_data = get_image_from_opus(opus_file)

    if image_data:
        output_path = os.path.join(directory, "cover.jpg")
        try:
            with open(output_path, "wb") as f:
                f.write(image_data)
            print(f"  -> Extracted art to {output_path}")
        except OSError as e:
            print(f"  [!] Write failed: {e}")
    else:
        print("  [!] No embedded art found in Opus file.")

def main():
    print(f"Starting crawl on {MUSIC_DIR}...")
    for root, dirs, files in os.walk(MUSIC_DIR):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        # Check this folder
        if any(f.lower().endswith(".opus") for f in files):
            process_directory(root)

    print("Done.")

if __name__ == "__main__":
    main()

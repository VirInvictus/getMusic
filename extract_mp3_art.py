#!/usr/bin/env python3

import os
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

# Configuration
MUSIC_DIR = os.path.expanduser("~/Music")
COVER_NAMES = ["cover.jpg", "folder.jpg", "front.jpg", "album.jpg"]

def get_image_from_mp3(filepath):
    """
    Extracts binary image data from an MP3's ID3 tags.
    Looks for the 'APIC' frame.
    """
    try:
        audio = MP3(filepath)
        if audio.tags is None:
            return None

        # Iterate through tags to find the APIC frame (Album Picture)
        # We look at .values() because the keys can be messy (e.g., "APIC:Desc")
        for tag in audio.tags.values():
            if tag.FrameID == 'APIC':
                # strict mode: check if tag.type == 3 (Front Cover)
                # pragmatic mode: just return the first image we find
                return tag.data

    except Exception as e:
        print(f"  [!] Error reading file {filepath}: {e}")

    return None

def process_directory(directory):
    # 1. Check if art already exists
    for name in COVER_NAMES:
        if os.path.exists(os.path.join(directory, name)):
            return

    # 2. Find the first MP3 file
    mp3_file = None
    for file in os.listdir(directory):
        if file.lower().endswith(".mp3"):
            mp3_file = os.path.join(directory, file)
            break

    if not mp3_file:
        return

    # 3. Extract and Write
    print(f"[+] Processing: {directory}")
    image_data = get_image_from_mp3(mp3_file)

    if image_data:
        output_path = os.path.join(directory, "cover.jpg")
        try:
            with open(output_path, "wb") as f:
                f.write(image_data)
            print(f"  -> Extracted art to {output_path}")
        except OSError as e:
            print(f"  [!] Write failed: {e}")
    else:
        print("  [!] No embedded art found in MP3 file.")

def main():
    print(f"Starting MP3 crawl on {MUSIC_DIR}...")
    for root, dirs, files in os.walk(MUSIC_DIR):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        # Check this folder
        if any(f.lower().endswith(".mp3") for f in files):
            process_directory(root)

    print("Done.")

if __name__ == "__main__":
    main()

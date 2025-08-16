Requires mutagen to run.

Run:
```pip install mutagen```

This was created out of a necessity when I began managing my large (5000+ song) libraries in foobar2000 rather than relying on MusicBee or MediaMonkey or iTunes. I use foobar2000's quicktagger component to rate each song. The library is in the format of **~\music\ARTIST NAME\ALBUM NAME** and the script relies on that structure to formulate the output .txt file.

You do not need to specify the directory if the file is sitting in your music library. It will automatically check every subdirectory for both options with no arguments.

Currently takes about 1 minute to write about 6000 songs.

```
getMusic.py — Two tools in one:
  1) Music library tree writer (tags + ratings)  ->  --library
  2) FLAC integrity checker (flac/ffmpeg)       ->  --checkFLAC

Examples:
  # Write a music library tree for the current directory
  python getMusic.py --library --root "." --output music_library.txt

  # Check FLACs under D:\Music and write problematic files to CSV
  python getMusic.py --checkFLAC --root "D:\Music" --output flac_errors.csv --workers 6 --prefer flac
```

If this helped you out, consider donating to me: (ko-fi.com/vrnvctss)[click here]

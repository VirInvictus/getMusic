<p align="center">
  <img src="assets/logo.png" alt="getMusic.py logo" width="220">
</p>

```getMusic.py``` is a lightweight CLI tool for music collectors — it builds a text library tree and checks FLAC/MP3 files for corruption.

This was created out of a necessity when I began managing my large (5000+ song) libraries in foobar2000 rather than relying on MusicBee or MediaMonkey or iTunes. I use foobar2000's quicktagger component to rate each song. The library is in the format of **~\music\ARTIST NAME\ALBUM NAME** and the script relies on that structure to formulate the output .txt file.

### Sample output in library

```
ARTIST: Ólafur Arnalds
  ├── ALBUM: Found Songs
      ├── SONG: 01. Ólafur Arnalds — Erla's Waltz (flac) [★★★★★ 5.0/5]
      ├── SONG: 02. Ólafur Arnalds — Raein (flac) [★★★★★ 5.0/5]
      ├── SONG: 03. Ólafur Arnalds — Romance (flac) [★★★★★ 5.0/5]
      ├── SONG: 04. Ólafur Arnalds — Allt varð hljótt (flac) [★★★★★ 5.0/5]
      ├── SONG: 05. Ólafur Arnalds — Lost Song (flac) [★★★★★ 5.0/5]
      ├── SONG: 06. Ólafur Arnalds — Faun (flac) [★★★★★ 5.0/5]
      └── SONG: 07. Ólafur Arnalds — Ljósið (flac) [★★★★★ 5.0/5]
```

### Requirements
Requires mutagen and tqdm to run. Requires FLAC and ffmpeg to be installed (the script will check for both). And, of course, requires Python to be installed. (I installed flac via ```winget install flac ffmpeg``` in Powershell). The Python libraries can be installed by simply running:
```pip install mutagen tqdm```

### Functions

#### Build a clean text library
```python getMusic.py --library```

#### Check FLACs for corruption
```python getMusic.py --testFLAC```

#### Check MP3s for decode errors
```python getMusic.py --testMP3```

#### Help File
```python getMusic.py --help``` displays:
```
usage: getMusic.py [-h] [--library | --testFLAC | --testMP3] [--root ROOT] [--output OUTPUT] [--workers WORKERS]
                   [--prefer {flac,ffmpeg}] [--quiet] [--only-errors | --no-only-errors] [--ffmpeg FFMPEG] [--verbose]

Music library tree, FLAC integrity, and MP3 decode checker

options:
  -h, --help            show this help message and exit
  --library             Generate library tree
  --testFLAC            Verify FLAC files and report failures
  --testMP3             Verify MP3 files and report decode errors/warnings
  --root ROOT           Root directory to scan (default: current dir)
  --output OUTPUT       Output path (library: text, FLAC/MP3: CSV)
  --workers WORKERS     Parallel workers for FLAC/MP3 (default: 4)
  --prefer {flac,ffmpeg}
                        Preferred tester if both available (for --testFLAC)
  --quiet               Reduce console output and hide progress bars (all modes)
  --only-errors, --no-only-errors
                        Write only rows with status != ok (MP3 mode; default: true)
  --ffmpeg FFMPEG       Path to ffmpeg (for --testMP3; otherwise uses PATH)
  --verbose             Verbose output; include OK rows (MP3 mode)
```

Running with no arguments provides a CLI-based menu.

## Support
If this project saved you time, consider [buying me a coffee](https://ko-fi.com/vrnvctss).

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]()
[![Ko-fi](https://img.shields.io/badge/support-Ko--fi-ff5f5f?logo=kofi)](https://ko-fi.com/vrnvctss)

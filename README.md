<p align="center">
  <img src="assets/logo.png" alt="getMusic.py logo" width="220">
</p>


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
```python getMusic.py --help``` for console instructions.

```python getMusic.py --library``` creates a running .txt file that is both aesthetically appealing and organized (and easily analyzed by Google's Gemini Pro 2.5 and its 1m context units). My 5000 song library with ratings and full names creates a .txt sitting at about \~500kb, equalling about \~160-170k context tokens.

```python getMusic.py --testFLAC``` searches the Music subfolders for all FLAC files and creates a .csv file with all the FLAC files with errors.
```python getMusic.py --testMP3``` does the same for MP3s

Running with no arguments provides a CLI-based menu.

## Support
If this project saved you time, consider [buying me a coffee](https://ko-fi.com/vrnvctss).


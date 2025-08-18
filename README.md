Requires mutagen and tqdm to run. Requires FLAC and ffmpeg to be installed (the script will check for both). And, of course, requires Python to be installed. (I installed flac via ```winget install flac ffmpeg``` in Powershell)

<p align="center">
  <img src="assets/logo.jpg" alt="getMusic.py logo" width="220">
</p>

Run:
```pip install mutagen tqdm```

This was created out of a necessity when I began managing my large (5000+ song) libraries in foobar2000 rather than relying on MusicBee or MediaMonkey or iTunes. I use foobar2000's quicktagger component to rate each song. The library is in the format of **~\music\ARTIST NAME\ALBUM NAME** and the script relies on that structure to formulate the output .txt file.

```python getMusic.py --help``` for console instructions.

```python getMusic.py --library``` creates a running .txt file that is both aesthetically appealing and organized (and easily analyzed by Google's Gemini Pro 2.5 and its 1m context units). My 5000 song library with ratings and full names creates a .txt sitting at about \~500kb, equalling about \~160-170k context tokens.

```python getMusic.py --testFLAC``` searches the Music subfolders for all FLAC files and creates a .csv file with all the FLAC files with errors.

Running with no arguments provides a CLI-based menu.

You do not need to specify the directory if the file is sitting in your music library. It will automatically check every subdirectory for both options with no arguments.

If this helped you out, consider donating to me: [click here](https://ko-fi.com/vrnvctss)

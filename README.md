Requires mutagen to run.

Run:
```pip install mutagen```

This was created out of a necessity when I began managing my large (5000+ song) libraries in foobar2000 rather than relying on MusicBee or MediaMonkey or iTunes. I use foobar2000's quicktagger component to rate each song. The library is in the format of **~\music\ARTIST NAME\ALBUM NAME** and the script relies on that structure to formulate the output .txt file.

```getMusic.py``` creates a running .txt file that is both aesthetically appealing and organized (and easily analyzed by Google's Gemini Pro 2.5 and its 1m context units. My 5000 song library with ratings and full names creates a .txt sitting at about ~500kb, equalling about 160-170~ context tokens.

```checkFLAC.py``` searches the Music subfolders for all FLAC files and creates a .csv file with all the FLAC files with errors.

You do not need to specify the directory if the file is sitting in your music library. It will automatically check every subdirectory for both options with no arguments.

If this helped you out, consider donating to me: [click here](https://ko-fi.com/vrnvctss)

# ShiYing (拾影)

**A free, open-source Windows video downloader with a GUI.** Paste a link to download videos from
YouTube, Bilibili, X (Twitter) and Douyin (Chinese TikTok, without watermark). Downloads use multiple
connections like IDM, and English videos can get AI-generated Chinese subtitles (local Whisper
speech recognition + DeepL or any OpenAI-compatible LLM for translation).

It is a graphical frontend for [yt-dlp](https://github.com/yt-dlp/yt-dlp), plus its own Douyin parser
and download engine. The interface is in Chinese.

[中文说明](README.md) · [Download](https://github.com/Jaqennn/ShiYing/releases/latest)

![ShiYing](docs/screenshot.png)

## Features

| Site | How | Notes |
|---|---|---|
| YouTube | yt-dlp + Deno | Up to 4K; signed-in cookies may be needed above 1080p |
| Bilibili | yt-dlp | 1080p+ needs your cookies; 4K needs a premium account |
| X / Twitter | yt-dlp | Highest quality without login |
| Douyin | built-in parser | No watermark, image posts, no login needed |
| Others | yt-dlp | Anything yt-dlp supports |

- **Batch**: paste several links at once (lines, spaces, even glued together), parse them in parallel,
  pick one quality for all, download everything with one click
- **Fast**: files are split into chunks and fetched over 16 connections; HLS/DASH falls back to
  yt-dlp's concurrent fragments
- **Compatible output**: prefers H.264 + AAC merged into MP4 so files play everywhere
- **Chinese subtitles**: whisper.cpp runs locally (CUDA build for NVIDIA GPUs), DeepL or an LLM translates,
  and `.zh.srt` / `.zh-en.srt` are written next to the video so players like PotPlayer load them
  automatically. Existing human-made YouTube captions are used instead of speech recognition when present
- Recognizes share text from Douyin and Bilibili apps, watches the clipboard, drag & drop
- One-click install and update of yt-dlp, ffmpeg, Deno and whisper.cpp

## Run

Download `ShiYing-windows-x64.zip` from Releases, unzip, open `ShiYing.exe`, then install the components
in Settings. Requires Windows 10/11 with WebView2.

From source (Python 3.12): `run.bat`. Build the exe: `build.bat`. Tests: `python -m pytest tests -q`.

## About

Built entirely with AI (Claude) from the author's own idea log: the author describes what they need and
tests it on their own PC. Sister project: [LiteCast](https://github.com/Jaqennn/LiteCast), a free AirPlay +
DLNA casting receiver for Android TV / Sony Bravia.

For personal use only; respect each site's terms and creators' rights. MIT License.

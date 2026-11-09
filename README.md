# YT İndir

Single-file Turkish YouTube MP3/MP4 desktop application for https://github.com/medre22932-png/youtube_indir.
All application code and the pinned yt-dlp verification key are in `youtube_indir.py`.
The remaining supporting files are needed for dependency installation, GitHub builds, or documentation.

## Download and use

Download from this repository's **Releases → latest** page:

- Windows x64: download and launch `YTIndir.exe` directly (single executable).
- macOS: extract `YTIndir-macOS.zip` and move `YT İndir.app` to Applications.

Python, Qt, FFmpeg, ffprobe and Python package dependencies are included by GitHub Actions. Users do not compile packages, install Python, use pip or install FFmpeg. The first launch needs internet access for the official yt-dlp nightly executable and Deno. macOS builds are currently unsigned and unnotarized.

Paste YouTube links, choose Audio (MP3, the startup default) or Video (MP4), and click **İndir**. The save folder is remembered. Optional quality controls are under **Kalite ve ayarlar**; update, retry, cleanup and diagnostic actions are in **Diğer**. The download list appears when videos are added. Downloads run sequentially with conversion and ffprobe validation before advancing. Each row has a **Kaldır** button, disabled while that item is being processed; removing a row does not delete its downloaded file. The app supports stopping after the current item, cancellation and retrying failures. Closing stops the active download and returns it to the queue. Only waiting and interrupted items are restored on reopening; completed, failed and cancelled items are session history and are not restored. Only individual videos are downloaded.

## Download speed and Adobe-compatible H.264 transcoding

Direct HTTP streams use 8 concurrent 4 MiB range connections (with automatic fallback to yt-dlp's downloader using 8 concurrent DASH/HLS fragments and 10 MiB HTTP chunks). For videos above 1080p (1440p/2K and 2160p/4K, where YouTube serves VP9 or AV1), the app transcodes video on the fly during the download into Adobe Premiere Pro / After Effects compatible H.264 (`avc1`, `yuv420p`) and AAC (`mp4a`) inside an `.mp4` container with `faststart`. Hardware GPU encoders (`h264_videotoolbox` on macOS, `h264_nvenc`, `h264_qsv`, and `h264_amf` on Windows/Linux) are probed at 4K and used automatically when available, with an automatic fallback to universal CPU encoding (`libx264 -preset superfast -crf 20`) and local-file transcoding if piping or hardware encoding fails on any PC. If a stream reports less than 256 KiB/s continuously for 20 seconds, the app reconnects with fresh video URLs and resumes after a short pause (5 seconds, then 10 seconds, up to two reconnects per job).

On a slow-connection reconnect, the parallel downloader reuses completed chunks only when the video, selected stream and total size match. It checks each HTTP response's byte range and length before saving it. Existing source bytes are replayed into a new FFmpeg process, so encoding restarts while network transfers resume. Closing waits for active workers to exit before destroying the window; a pending network read can briefly delay closure.

## Download a section

Enable **Yalnızca belirli bir bölümü indir** and enter a start and end time, for example `01:00` to `01:30`. Times accept seconds, `MM:SS`, or `HH:MM:SS`, including milliseconds. This works for both MP4 and MP3. The same interval applies to all links added together; each queued item saves its own interval. Disable the option to download the full video again.

For sections, the app downloads the complete source using yt-dlp’s normal downloader, then cuts the requested interval locally with FFmpeg. MP4 downloads retain the selected resolution; MP3 downloads fetch the full audio source and encode only the selected interval. Each download uses a private temporary folder under `downloads/` beside `settings.json`, including full downloads. Only verified final files are moved to the selected save folder. The clip is verified before being moved into the save folder, and the full source and temporary files are removed when the job finishes or is cancelled. Leftovers from interrupted or crashed runs are cleaned on the next launch; failed and cancelled jobs are cleaned when they finish. This requires bandwidth and temporary disk space for the full source in the settings location. Download progress shows transferred MB; the local cutting step shows saved MB, processed duration and an ETA when available.

MP4 cuts copy the encoded streams without re-encoding; cut boundaries may differ from the requested times because of keyframes. Output names include the selected interval.

## Saved settings and updates

Like the translation application's StateManager, all mutable state lives outside the Python file/executable/app:

- macOS: `~/Library/Application Support/YTIndir/`
- Windows: `%APPDATA%\YTIndir\`
- Linux source runs: `$XDG_DATA_HOME/YTIndir/` (default `~/.local/share/YTIndir/`).

Override with `YTINDIR_DATA_DIR` for a portable or isolated installation.

The folder contains `settings.json` (output folder, format, quality, window geometry), `queue.json`, versioned `engine/` downloads and `tools/deno`. Settings and queue are written atomically. Replacing the app or downloading a new version preserves this folder. One instance per folder is enforced.

The app checks yt-dlp nightly and Deno at startup and before each batch, and checks again hourly between items. yt-dlp updates require a valid pinned signature, SHA-256 checksum and successful executable check before atomically activating the new version; the previous engine remains available. Deno downloads require GitHub's SHA-256 asset digest and an executable check. Updates run in the writable folder, including from a Windows one-file EXE or a macOS app. They never install packages into a frozen app. If update checks fail, users can explicitly continue with a previously installed engine and Deno.

FFmpeg and the Python/Qt packages are updated by rebuilding the app on GitHub. **Uygulama sürümleri** opens this repository's releases; replace the app with the new download. The app does not overwrite its running EXE or macOS bundle.

## GitHub builds

`.github/workflows/build.yml` runs when application/build files change on `main`, or by manual dispatch. No version tag is required. Separate native runners build Windows x64 (`YTIndir.exe`) and macOS (`YTIndir-macOS.zip`) packages with embedded static FFmpeg/ffprobe binaries.

Like the translation project, each runner uploads directly to the `latest` GitHub Release (`YTIndir.exe` and `YTIndir-macOS.zip`), and GitHub automatically displays the SHA-256 digest for each asset. The workflow never uploads or downloads Actions build artifacts; its preparation step removes artifacts left by previous workflows. Python dependency caching remains enabled. Local edits do not trigger GitHub builds until you push them.

macOS signing/notarization is not configured. Include licenses and corresponding-source access for redistributed FFmpeg and other bundled libraries before public binary distribution.

## Source development

Python 3.11+ and FFmpeg/ffprobe are needed only for source development:

```sh
python -m pip install -r requirements.txt
python youtube_indir.py
```

The application source can be copied as one `youtube_indir.py`; no `downloader` package, key file or resource folder is required. Source runs can also use FFmpeg/ffprobe placed in the saved folder's `tools/` directory. Build binaries through GitHub Actions.

```sh
QT_QPA_PLATFORM=offscreen YTINDIR_DATA_DIR=/tmp/ytindir-preview python youtube_indir.py --smoke-test
YTINDIR_DATA_DIR=/tmp/ytindir-check python youtube_indir.py --check-bundle
```

GUI smoke and bundle checks do not use the network. Windows and Mac package compatibility is checked by the workflow; a local source test alone does not establish cross-platform binary compatibility.

## Minimal project files

- `youtube_indir.py`: complete application; no other project Python file is needed.
- `requirements.txt`: third-party dependencies for running the source.
- `YTIndir.spec` and `.github/workflows/build.yml`: Windows/macOS packaging on GitHub.
- `README.md` and `.gitignore`: documentation and repository housekeeping.

The Python file still needs its third-party packages and FFmpeg/ffprobe when run from source. GitHub-built EXE/app packages include these dependencies. A local `.venv/`, if present, is retained so source runs continue working; it is excluded from Git.

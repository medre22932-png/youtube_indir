# YT İndir

Single-file Turkish YouTube MP3/MP4 desktop application for https://github.com/medre22932-png/youtube_indir.
All application code and the pinned yt-dlp verification key are in `youtube_indir.py`.
The remaining supporting files are needed for dependency installation, GitHub builds, or documentation.

## Download and use

Download from this repository's **Releases → latest** page:

- Windows x64: download and launch `YTIndir.exe` directly (single executable).
- Apple Silicon: extract `YTIndir-macos-arm64.zip` and move `YT İndir.app` to Applications.

Python, Qt, FFmpeg, ffprobe and Python package dependencies are included by GitHub Actions. Users do not compile packages, install Python, use pip or install FFmpeg. The first launch needs internet access for the official yt-dlp nightly executable and Deno. macOS builds are currently unsigned and unnotarized.

Paste YouTube links, choose Audio (MP3, the startup default) or Video (MP4), and click **İndir**. The save folder is remembered. Optional quality controls are under **Kalite ve ayarlar**; update, retry, cleanup and diagnostic actions are in **Diğer**. The download list appears when videos are added. Downloads run sequentially with conversion and ffprobe validation before advancing. Each row has a **Kaldır** button, disabled while that item is being processed; removing a row does not delete its downloaded file. The app supports stopping after the current item, cancellation and retrying failures. Closing stops the active download and returns it to the queue. Only waiting and interrupted items are restored on reopening; completed, failed and cancelled items are session history and are not restored. Only individual videos are downloaded.

## Download a section

Enable **Yalnızca belirli bir bölümü indir** and enter a start and end time, for example `01:00` to `01:30`. Times accept seconds, `MM:SS`, or `HH:MM:SS`, including milliseconds. This works for both MP4 and MP3. The same interval applies to all links added together; each queued item saves its own interval. Disable the option to download the full video again.

The app uses yt-dlp's `--download-sections` with the FFmpeg downloader to seek in the remote media and retrieve the requested section, rather than downloading a full file and trimming it locally. Servers and stream formats may require extra headers, index data or nearby segments; exact network usage depends on the source. MP4 sections are re-encoded for accurate cut boundaries. MP3 sections use the selected audio bitrate. Output names include the interval so clips cannot collide with full downloads or other intervals.

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

`.github/workflows/build.yml` runs when application/build files change on `main`, or by manual dispatch. No version tag is required. Separate native runners build Windows x64 and Apple Silicon (macOS ARM64) packages, then check each packaged GUI and embedded FFmpeg/ffprobe. Checks launch the packaged app through Python with timeouts and captured output, so failures report the actual test and native exit code. On Windows, the console hides on a normal double-click launch while CI retains diagnostic output.

The Windows GUI check uses Qt's native `windows` platform plugin; macOS CI uses `offscreen`. The smoke report records the active platform and CI verifies it. Smoke runs also write startup/capture/shutdown stages and native fault traces to `smoke.log` in the test data folder; CI prints this file even when the app fails or times out. Qt plugin diagnostics are enabled during packaged checks. A screenshot that cannot be saved fails the smoke test.

The smoke capture timer belongs to the window and is disconnected after the event loop exits. The window's deferred deletion is then processed while `QApplication` is still alive, before releasing the application lock. This makes GUI teardown explicit for both source and packaged runs.

Like the translation project, each runner uploads directly to the `latest` GitHub Release. The workflow never uploads or downloads Actions build artifacts; its preparation step removes artifacts left by previous workflows. Python dependency caching remains enabled.

The workflow recreates `latest` as a draft, uploads `YTIndir.exe`, `YTIndir-macos-arm64.zip`, the single Python source and SHA-256 checksums, and publishes the release after both platform jobs succeed. A failed build leaves the new release as a draft. The previous `latest` release is replaced during preparation. Runs are serialized to prevent overlapping uploads. Local edits do not trigger GitHub builds until you push them.

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

# YT İndir

Single-file Turkish YouTube MP3/MP4 desktop application for https://github.com/medre22932-png/youtube_indir.
All application code and the pinned yt-dlp verification key are in `youtube_indir.py`.
The remaining supporting files are needed for dependency installation, GitHub builds, or documentation.

## Download and use

Download a platform ZIP from this repository's **Releases** page, or the **Build desktop apps** workflow artifacts:

- Windows x64: extract and launch `YTIndir.exe` (single executable).
- Apple Silicon: extract `YTIndir-macos-arm64.zip` and move `YT İndir.app` to Applications.
- Intel Mac: use `YTIndir-macos-x64.zip`.

Python, Qt, FFmpeg, ffprobe and Python package dependencies are included by GitHub Actions. Users do not compile packages, install Python, use pip or install FFmpeg. The first launch needs internet access for the official yt-dlp nightly executable and Deno. macOS builds are currently unsigned and unnotarized.

Paste YouTube links, choose MP3/MP4 and quality, select an output folder, and add them to the queue. Downloads run sequentially with conversion and ffprobe validation before advancing. The app supports stopping after the current item, cancellation, retrying failures and restoring the queue. Only individual videos are downloaded.

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

`.github/workflows/build.yml` runs on pushes to `main`, pull requests, version tags and manual dispatch. Separate native runners build Windows x64, macOS ARM64 and macOS Intel packages. The workflow installs dependencies and FFmpeg on GitHub, checks the source, packages the app with PyInstaller and checks the packaged GUI and embedded FFmpeg/ffprobe. All three archives must build successfully before a `v*` tag publishes a release with SHA-256 checksums. Push a version tag such as `v1.0.0` to publish; regular pushes only create workflow artifacts.

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

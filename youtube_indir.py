"""YT İndir — single-file desktop application. Builds run in GitHub Actions."""
from __future__ import annotations

import sys
import os

# Windowed Windows executables have no console streams.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "resources"


def child_environment() -> dict[str, str]:
    env = os.environ.copy()
    # A second PyInstaller executable must start as an independent application.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def run_tool(args: list[str], timeout: int = 30) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, env=child_environment(), **options)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1800:] or "Araç başlatılamadı.")
    return result.stdout.strip()


def engine_asset(system: str | None = None, machine: str | None = None) -> str:
    system, machine = system or sys.platform, (machine or platform.machine()).lower()
    if system == "darwin":
        return "yt-dlp_macos"
    if system == "win32":
        return "yt-dlp_arm64.exe" if machine in ("arm64", "aarch64") else "yt-dlp.exe"
    if system.startswith("linux"):
        suffix = "_aarch64" if machine in ("arm64", "aarch64") else ""
        if machine not in ("arm64", "aarch64", "x86_64", "amd64"):
            raise RuntimeError("Bu işlemci için otomatik indirme motoru kurulumu desteklenmiyor.")
        libc = "musllinux" if platform.libc_ver()[0] == "musl" else "linux"
        return f"yt-dlp_{libc}{suffix}"
    raise RuntimeError("Bu işletim sistemi desteklenmiyor.")


def find_tool(name: str, data_dir: Path) -> Path | None:
    executable = name + (".exe" if os.name == "nt" else "")
    candidates = [data_dir / "tools" / executable, resource_dir() / "bin" / executable]
    if path := shutil.which(name):
        candidates.append(Path(path))
    if sys.platform == "darwin":
        candidates += [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def parse_links(text: str) -> list[str]:
    links = text.split()
    if not links:
        raise ValueError("En az bir YouTube bağlantısı girin.")
    for link in links:
        url = urlparse(link)
        host = (url.hostname or "").lower()
        if (url.scheme not in ("https", "http") or url.username or url.password
                or host not in ("youtube.com", "www.youtube.com", "m.youtube.com",
                                "music.youtube.com", "youtu.be", "www.youtu.be")):
            raise ValueError(f"Geçersiz YouTube bağlantısı: {link}")
    return links


@dataclass
class Job:
    url: str
    format: str
    quality: str
    folder: str
    id: str = field(default_factory=lambda: uuid4().hex)
    title: str = "Bağlantı bilgisi bekleniyor"
    status: str = "Bekliyor"
    progress: int = 0
    output: str = ""
    error: str = ""
    start_time: float | None = None
    end_time: float | None = None


def parse_timestamp(value: str) -> float:
    """Accept seconds, MM:SS or HH:MM:SS, including fractional seconds."""
    value = value.strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?", value):
        raise ValueError("Zamanı saniye, DD:SS veya SS:DD:SS olarak girin. Örnek: 01:30")
    parts = value.split(":")
    if len(parts) > 1 and any(float(part) >= 60 for part in parts[1:]):
        raise ValueError("Dakika ve saniye alanları 60'tan küçük olmalı.")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def timestamp_seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def clip_bounds(start: str, end: str) -> tuple[float, float]:
    beginning, ending = parse_timestamp(start), parse_timestamp(end)
    if ending <= beginning:
        raise ValueError("Bitiş zamanı başlangıç zamanından sonra olmalı.")
    return beginning, ending



ACTIVE_STATUSES = {"İndiriliyor", "Dönüştürülüyor", "Bilgiler alınıyor", "Doğrulanıyor"}

def save_jobs(path: Path, jobs: list[Job]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(j) for j in jobs if j.status == "Bekliyor" or j.status in ACTIVE_STATUSES], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_jobs(path: Path) -> list[Job]:
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    for entry in entries:
        entry.pop("accurate_cut", None)  # Compatibility with previously saved queues.
    jobs = [Job(**j) for j in entries]
    jobs = [job for job in jobs if job.status == "Bekliyor" or job.status in ACTIVE_STATUSES]
    for job in jobs:
        if job.status in ACTIVE_STATUSES:
            job.status, job.progress, job.error, job.output = "Bekliyor", 0, "", ""
    return jobs


def video_output_stem(job: Job, title: str, video_id: str) -> str:
    """Use a portable title-based name even when yt-dlp omits _filename."""
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip().rstrip(".")
    title = title.encode("utf-8")[:140].decode("utf-8", "ignore").rstrip(" .") or "YouTube video"
    if re.match(r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title):
        title = "_" + title
    video_id = re.sub(r"[^\w-]", "_", video_id) or job.id
    section = (f" [{timestamp_seconds(job.start_time)}-{timestamp_seconds(job.end_time)}s]"
               if job.start_time is not None and job.end_time is not None else "")
    return f"{title} [{video_id}] [{job.format}-{job.quality}]{section}"


def download_arguments(job: Job, ffmpeg: Path, deno: Path, source_folder: Path | None = None) -> list[str]:
    args = ["--ignore-config", "--no-playlist", "--newline", "--no-colors", "--progress",
            "--encoding", "utf-8", "--socket-timeout", "20", "--retries", "3",
            "--fragment-retries", "3", "--extractor-retries", "3", "--continue",
            # Parallelism applies to DASH/HLS fragments; parallel HTTP range workers
            # handle direct streams with automatic fallback to yt-dlp chunked HTTP.
            "--concurrent-fragments", "8", "--http-chunk-size", "10M",
            "--retry-sleep", "http:exp=1:8", "--retry-sleep", "fragment:exp=1:8",
            "--progress-delta", "0.5",
            "--no-overwrites", "--windows-filenames", "--ffmpeg-location", str(ffmpeg.parent),
            "--no-js-runtimes", "--js-runtimes", f"deno:{deno}",
            "--progress-template", "download:__PROGRESS__%(progress)j",
            "--progress-template", "postprocess:__POST__%(progress)j",
            "--print", "before_dl:__TITLE__%(title)j", "--print", "after_move:__FILE__%(filepath)j",
            "--print", "before_dl:__STREAMS__%(requested_formats.:.format_id)j",
            "--print", "before_dl:__DURATION__%(duration)j",
            "--no-simulate", "-P", str(source_folder or job.folder)]
    # Quality is part of the name so changing it cannot silently reuse another output.
    section = ""
    if job.start_time is not None or job.end_time is not None:
        if (job.start_time is None or job.end_time is None
                or not 0 <= job.start_time < job.end_time < float("inf")):
            raise ValueError("Geçersiz kesit aralığı.")
        section = f" [{timestamp_seconds(job.start_time)}-{timestamp_seconds(job.end_time)}s]"
    template = f"%(title).140B [%(id)s] [{job.format}-{job.quality}]{section}.%(ext)s"
    args += ["-o", template]
    if job.format == "MP3":
        args += ["-f", "bestaudio/best", "--embed-metadata"]
        if not section:
            args += ["-x", "--audio-format", "mp3", "--audio-quality", f"{job.quality}K"]
    else:
        limit = "" if job.quality == "best" else f"[height<={int(job.quality)}]"
        high_res = f"bv*[height>1080]{limit}[vcodec!^=av01]+ba/" if job.quality == "best" or int(job.quality) > 1080 else ""
        args += ["-f", f"{high_res}bv*{limit}+ba/b{limit}",
                 "-S", "res,vcodec:h264,acodec:m4a",
                 "--merge-output-format", "mp4", "--remux-video", "mp4"]
    return args + ["--", job.url]


CPU_H264_ARGS = ["-c:v", "libx264", "-preset", "superfast", "-crf", "20", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]
_ENCODER_CACHE: dict[str, list[tuple[str, list[str]]]] = {}


def h264_encoder_candidates(system: str | None = None) -> list[tuple[str, list[str]]]:
    """Ordered H.264 encoders for Adobe compatibility (GPU first, universal CPU fallback last)."""
    system = system or sys.platform
    candidates: list[tuple[str, list[str]]] = []
    if system == "darwin":
        candidates += [
            ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-q:v", "65", "-allow_sw", "1", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]),
            ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "20M", "-allow_sw", "1", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]),
        ]
    elif system == "win32" or system.startswith("linux"):
        candidates += [
            ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]),
            ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "fast", "-qp", "20", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]),
            ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "20", "-pix_fmt", "nv12", "-tag:v", "avc1"]),
            ("h264_amf", ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]),
        ]
        if system.startswith("linux"):
            candidates.append(("h264_vaapi", ["-c:v", "h264_vaapi", "-qp", "20", "-tag:v", "avc1"]))
    candidates.append(("libx264", list(CPU_H264_ARGS)))
    return candidates


def available_h264_encoders(ffmpeg: Path | None) -> list[tuple[str, list[str]]]:
    """Probe and cache working 4K-capable H.264 encoders, always ending with CPU libx264 fallback."""
    fallback = ("libx264", list(CPU_H264_ARGS))
    if not ffmpeg or not ffmpeg.is_file():
        return [fallback]
    key = str(ffmpeg.resolve())
    if key in _ENCODER_CACHE:
        return list(_ENCODER_CACHE[key])
    working: list[tuple[str, list[str]]] = []
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for name, encoder_args in h264_encoder_candidates():
        if name == "libx264":
            continue
        cmd = [
            str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=3840x2160:r=30:d=0.04",
            "-frames:v", "1", *encoder_args, "-f", "null", "-",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=5, env=child_environment(), **options)
            if res.returncode == 0:
                working.append((name, list(encoder_args)))
                break
        except (OSError, subprocess.SubprocessError):
            continue
    working.append(fallback)
    _ENCODER_CACHE[key] = working
    return list(working)


def probe_media_codecs(ffprobe: Path | None, source: Path) -> tuple[str, str]:
    """Return (video_codec, audio_codec) in lowercase, or ('', '') if unavailable."""
    if not ffprobe or not ffprobe.is_file() or not source.is_file():
        return "", ""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        res = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, env=child_environment(), **options,
        )
        if res.returncode != 0:
            return "", ""
        streams = json.loads(res.stdout).get("streams", [])
        vcodec = next((s.get("codec_name", "") for s in streams
                       if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), "")
        acodec = next((s.get("codec_name", "") for s in streams if s.get("codec_type") == "audio"), "")
        return vcodec.lower(), acodec.lower()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", ""


def transcode_arguments(video_input: str, audio_input: str | None, output: Path,
                        encoder_args: list[str] | None = None, *, copy_audio: bool = True) -> list[str]:
    """Build FFmpeg arguments to produce an Adobe-compatible H.264 (avc1) + AAC MP4."""
    v_args = list(encoder_args) if encoder_args else list(CPU_H264_ARGS)
    a_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
    args = ["-hide_banner", "-y", "-threads", "0", "-i", video_input]
    if audio_input:
        args += ["-i", audio_input, "-map", "0:v:0", "-map", "1:a:0"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0?"]
    args += [*v_args, *a_args, "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output)]
    return args


def cut_arguments(job: Job, source: Path, output: Path,
                  video_codec_args: list[str] | None = None, *, copy_audio: bool = True) -> list[str]:
    if job.start_time is None or job.end_time is None or not 0 <= job.start_time < job.end_time < float("inf"):
        raise ValueError("Geçersiz kesit aralığı.")
    args = ["-hide_banner", "-nostdin", "-y" if video_codec_args else "-n", "-ss", timestamp_seconds(job.start_time),
            "-i", str(source), "-t", timestamp_seconds(job.end_time - job.start_time),
            "-map_metadata", "0"]
    if job.format == "MP3":
        args += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", f"{int(job.quality)}k"]
    elif video_codec_args:
        audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
        args += ["-map", "0:v:0", "-map", "0:a:0?", *video_codec_args, *audio_args, "-movflags", "+faststart"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy"]
    return args + ["-progress", "pipe:1", "-nostats", str(output)]


def cleanup_downloads(root: Path) -> list[str]:
    """Remove only app-owned job folders left by interrupted runs."""
    errors = []
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob("job-*"):
        if path.is_dir() and not path.is_symlink():
            try:
                shutil.rmtree(path)
            except OSError as error:
                errors.append(str(error))
    return errors


import hashlib
import io
import json
import os
import platform
import re
import shutil
import tempfile
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import certifi
import pgpy
import requests
from PySide6.QtCore import QThread, Signal



APP_REPOSITORY = "medre22932-png/youtube_indir"
PUBLIC_KEY = '-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQINBGP78C4BEAD0rF9zjGPAt0thlt5C1ebzccAVX7Nb1v+eqQjk+WEZdTETVCg3\nWAM5ngArlHdm/fZqzUgO+pAYrB60GKeg7ffUDf+S0XFKEZdeRLYeAaqqKhSibVal\nDjvOBOztu3W607HLETQAqA7wTPuIt2WqmpL60NIcyr27LxqmgdN3mNvZ2iLO+bP0\nnKR/C+PgE9H4ytywDa12zMx6PmZCnVOOOu6XZEFmdUxxdQ9fFDqd9LcBKY2LDOcS\nYo1saY0YWiZWHtzVoZu1kOzjnS5Fjq/yBHJLImDH7pNxHm7s/PnaurpmQFtDFruk\nt+2lhDnpKUmGr/I/3IHqH/X+9nPoS4uiqQ5HpblB8BK+4WfpaiEg75LnvuOPfZIP\nKYyXa/0A7QojMwgOrD88ozT+VCkKkkJ+ijXZ7gHNjmcBaUdKK7fDIEOYI63Lyc6Q\nWkGQTigFffSUXWHDCO9aXNhP3ejqFWgGMtCUsrbkcJkWuWY7q5ARy/05HbSM3K4D\nU9eqtnxmiV1WQ8nXuI9JgJQRvh5PTkny5LtxqzcmqvWO9TjHBbrs14BPEO9fcXxK\nL/CFBbzXDSvvAgArdqqlMoncQ/yicTlfL6qzJ8EKFiqW14QMTdAn6SuuZTodXCTi\nInwoT7WjjuFPKKdvfH1GP4bnqdzTnzLxCSDIEtfyfPsIX+9GI7Jkk/zZjQARAQAB\ntDdTaW1vbiBTYXdpY2tpICh5dC1kbHAgc2lnbmluZyBrZXkpIDxjb250YWN0QGdy\ndWI0ay54eXo+iQJOBBMBCgA4FiEErAy75oSNaoc0ZK9OV89lkztadYEFAmP78C4C\nGwMFCwkIBwIGFQoJCAsCBBYCAwECHgECF4AACgkQV89lkztadYEVqQ//cW7TxhXg\n7Xbh2EZQzXml0egn6j8QaV9KzGragMiShrlvTO2zXfLXqyizrFP4AspgjSn/4NrI\n8mluom+Yi+qr7DXT4BjQqIM9y3AjwZPdywe912Lxcw52NNoPZCm24I9T7ySc8lmR\nFQvZC0w4H/VTNj/2lgJ1dwMflpwvNRiWa5YzcFGlCUeDIPskLx9++AJE+xwU3LYm\njQQsPBqpHHiTBEJzMLl+rfd9Fg4N+QNzpFkTDW3EPerLuvJniSBBwZthqxeAtw4M\nUiAXh6JvCc2hJkKCoygRfM281MeolvmsGNyQm+axlB0vyldiPP6BnaRgZlx+l6MU\ncPqgHblb7RW5j9lfr6OYL7SceBIHNv0CFrt1OnkGo/tVMwcs8LH3Ae4a7UJlIceL\nV54aRxSsZU7w4iX+PB79BWkEsQzwKrUuJVOeL4UDwWajp75OFaUqbS/slDDVXvK5\nOIeuth3mA/adjdvgjPxhRQjA3l69rRWIJDrqBSHldmRsnX6cvXTDy8wSXZgy51lP\nm4IVLHnCy9m4SaGGoAsfTZS0cC9FgjUIyTyrq9M67wOMpUxnuB0aRZgJE1DsI23E\nqdvcSNVlO+39xM/KPWUEh6b83wMn88QeW+DCVGWACQq5N3YdPnAJa50617fGbY6I\ngXIoRHXkDqe23PZ/jURYCv0sjVtjPoVC+bg=\n=bJkn\n-----END PGP PUBLIC KEY BLOCK-----\n'

REPOSITORY = "yt-dlp/yt-dlp-nightly-builds"


def verify_manifest(manifest: bytes, signature: bytes, key_path: Path | None = None) -> None:
    key, _ = (pgpy.PGPKey.from_file(str(key_path)) if key_path else pgpy.PGPKey.from_blob(PUBLIC_KEY))
    signed = pgpy.PGPSignature.from_blob(signature)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        if not key.verify(manifest, signed):
            raise RuntimeError("Güncellemenin dijital imzası doğrulanamadı.")


def manifest_digest(manifest: bytes, name: str) -> str:
    for line in manifest.decode("utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            return parts[0].lower()
    raise RuntimeError(f"Doğrulama listesinde dosya bulunamadı: {name}")


class EngineUpdater:
    """Installs verified, versioned executables, then atomically switches a pointer."""

    def __init__(self, root: Path, report=lambda message: None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.report = report
        self.session = requests.Session()
        self.session.verify = certifi.where()
        self.session.headers.update({"User-Agent": "YTIndir/1.0", "Accept": "application/vnd.github+json"})

    def close(self):
        self.session.close()

    def fetch(self, url: str, maximum: int = 5_000_000) -> bytes:
        data = bytearray()
        with self.session.get(url, timeout=(15, 60), stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(256 * 1024):
                data.extend(chunk)
                if len(data) > maximum:
                    raise RuntimeError("Güncelleme dosyası beklenen boyutu aşıyor.")
        return bytes(data)

    def release(self, repository: str) -> dict:
        return json.loads(self.fetch(f"https://api.github.com/repos/{repository}/releases/latest"))

    def asset(self, release: dict, name: str, repository: str) -> dict:
        entry = next((a for a in release["assets"] if a["name"] == name), None)
        if not entry:
            raise RuntimeError(f"Resmî sürümde dosya bulunamadı: {name}")
        prefix = f"https://github.com/{repository}/releases/download/"
        if not entry["browser_download_url"].startswith(prefix):
            raise RuntimeError("Beklenmeyen güncelleme adresi.")
        return entry

    def current(self) -> Path | None:
        try:
            state = json.loads((self.root / "engine.json").read_text())
            path = self.root / state["directory"] / engine_asset()
            if path.parent.parent == self.root and path.is_file():
                return path
        except (OSError, ValueError, KeyError):
            pass
        return None

    def update(self) -> tuple[Path, str]:
        self.report("Nightly güncellemeleri denetleniyor…")
        release = self.release(REPOSITORY)
        tag, name = release["tag_name"], engine_asset()
        if not re.fullmatch(r"[\w.\-]+", tag):
            raise RuntimeError("Geçersiz sürüm adı.")
        base = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
        manifest = self.fetch(base + "SHA2-256SUMS")
        verify_manifest(manifest, self.fetch(base + "SHA2-256SUMS.sig"))
        expected = manifest_digest(manifest, name)
        destination = self.root / tag / name
        old = self.current()
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            self.report(f"İndirme motoru güncelleniyor: {tag}")
            entry = self.asset(release, name, REPOSITORY)
            # Stage on the same filesystem; never overwrite the last working engine.
            with tempfile.TemporaryDirectory(prefix="update-", dir=self.root) as temporary:
                staged = Path(temporary) / name
                self.download(entry["browser_download_url"], staged, expected)
                staged.chmod(0o755)
                run_tool([str(staged), "--version"])
                destination.parent.mkdir(exist_ok=True)
                os.replace(staged, destination)
        version = run_tool([str(destination), "--version"])
        pointer = self.root / "engine.tmp"
        pointer.write_text(json.dumps({"directory": tag, "version": version,
                                      "previous": str(old) if old else None,
                                      "checked": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        os.replace(pointer, self.root / "engine.json")
        # Keep current + previous, preventing daily builds from accumulating forever.
        keep = {destination.parent, old.parent if old else destination.parent}
        for directory in self.root.iterdir():
            if directory.is_dir() and directory not in keep and re.fullmatch(r"[\d.]+", directory.name):
                shutil.rmtree(directory)
        return destination, version

    def download(self, url: str, path: Path, expected: str) -> None:
        digest, size = hashlib.sha256(), 0
        with self.session.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with path.open("wb") as output:
                for block in response.iter_content(256 * 1024):
                    size += len(block)
                    if size > 250_000_000:
                        raise RuntimeError("Güncelleme dosyası çok büyük.")
                    digest.update(block)
                    output.write(block)
        if digest.hexdigest() != expected:
            raise RuntimeError("Güncellemenin SHA-256 doğrulaması başarısız oldu.")

    def ensure_deno(self, data_dir: Path) -> Path:
        self.report("JavaScript motoru denetleniyor…")
        release = self.release("denoland/deno")
        existing = find_tool("deno", data_dir)
        if existing:
            try:
                if run_tool([str(existing), "--version"]).split()[1] == release["tag_name"].lstrip("v"):
                    return existing
            except (OSError, RuntimeError, IndexError):
                pass
        system = platform.system()
        arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        target = {"Darwin": "apple-darwin", "Windows": "pc-windows-msvc", "Linux": "unknown-linux-gnu"}[system]
        name = f"deno-{arch}-{target}.zip"
        asset = self.asset(release, name, "denoland/deno")
        digest = asset.get("digest") or ""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise RuntimeError("Deno için resmî SHA-256 özeti bulunamadı.")
        self.report("JavaScript motoru güncelleniyor…")
        tools = data_dir / "tools"
        tools.mkdir(exist_ok=True)
        executable = "deno.exe" if os.name == "nt" else "deno"
        with tempfile.TemporaryDirectory(dir=tools) as temporary:
            archive = Path(temporary) / name
            self.download(asset["browser_download_url"], archive, digest[7:])
            with zipfile.ZipFile(archive) as zipped:
                # Extract only the expected executable, never arbitrary archive paths.
                staged = Path(temporary) / executable
                staged.write_bytes(zipped.read(executable))
            staged.chmod(0o755)
            run_tool([str(staged), "--version"])
            os.replace(staged, tools / executable)
        return tools / executable


class UpdateThread(QThread):
    message = Signal(str)
    ready = Signal(str, str, str)
    failed = Signal(str)

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir

    def run(self):
        updater = EngineUpdater(self.data_dir / "engine", self.message.emit)
        try:
            engine, version = updater.update()
            deno = updater.ensure_deno(self.data_dir)
            self.ready.emit(str(engine), version, str(deno))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            updater.close()

def application_data_dir() -> Path:
    override = os.environ.get("YTINDIR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "YTIndir"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "YTIndir"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "YTIndir"


class StateManager:
    """File-backed settings, shared by source and packaged installations."""
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "settings.json"
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.state, dict):
                self.state = {}
        except (OSError, ValueError):
            self.state = {}

    def value(self, key, default=None):
        value = self.state.get(key, default)
        if key == "geometry" and isinstance(value, str):
            try:
                return bytes.fromhex(value)
            except ValueError:
                return b""
        return value

    def setValue(self, key, value):
        if key == "geometry":
            value = bytes(value).hex()
        self.state[key] = value
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


import concurrent.futures
from contextlib import ExitStack
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from requests.adapters import HTTPAdapter

from PySide6.QtCore import (
    QBuffer, QByteArray, QEasingCurve, QIODevice, QObject, QPointF, QPropertyAnimation,
    QProcess, QProcessEnvironment, QRectF, Qt, QThread, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QIcon, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QListView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QMenu, QScrollArea, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def render_icon_image(size: int = 256) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = size / 256.0
    painter.scale(scale, scale)

    bg_rect = QRectF(14, 14, 228, 228)
    bg_gradient = QLinearGradient(14, 14, 242, 242)
    bg_gradient.setColorAt(0.0, QColor("#1f2e47"))
    bg_gradient.setColorAt(0.55, QColor("#141d2e"))
    bg_gradient.setColorAt(1.0, QColor("#0d131f"))
    painter.setPen(QPen(QColor("#385179"), 4.5))
    painter.setBrush(bg_gradient)
    painter.drawRoundedRect(bg_rect, 54, 54)

    glow = QRadialGradient(QPointF(128, 118), 96)
    glow.setColorAt(0.0, QColor(57, 119, 232, 65))
    glow.setColorAt(1.0, QColor(57, 119, 232, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(128, 118), 96, 96)

    card_rect = QRectF(38, 44, 170, 114)
    card_grad = QLinearGradient(38, 44, 208, 158)
    card_grad.setColorAt(0.0, QColor("#ff475e"))
    card_grad.setColorAt(0.5, QColor("#f21b38"))
    card_grad.setColorAt(1.0, QColor("#c40d26"))
    painter.setPen(QPen(QColor(255, 255, 255, 50), 2.5))
    painter.setBrush(card_grad)
    painter.drawRoundedRect(card_rect, 30, 30)

    play = QPainterPath()
    play.moveTo(106, 75)
    play.lineTo(152, 101)
    play.lineTo(106, 127)
    play.closeSubpath()
    painter.setPen(QPen(QColor("#ffffff"), 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor("#ffffff"))
    painter.drawPath(play)

    badge_center = QPointF(170, 170)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111722"))
    painter.drawEllipse(badge_center, 50, 50)

    badge_grad = QLinearGradient(126, 126, 214, 214)
    badge_grad.setColorAt(0.0, QColor("#5ea0ff"))
    badge_grad.setColorAt(0.5, QColor("#3977e8"))
    badge_grad.setColorAt(1.0, QColor("#1f53b8"))
    painter.setPen(QPen(QColor("#8ab8ff"), 2.5))
    painter.setBrush(badge_grad)
    painter.drawEllipse(badge_center, 44, 44)

    arrow_pen = QPen(QColor("#ffffff"), 9.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(arrow_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(170, 144), QPointF(170, 177))
    chevron = QPainterPath()
    chevron.moveTo(154, 165)
    chevron.lineTo(170, 181)
    chevron.lineTo(186, 165)
    painter.drawPath(chevron)
    painter.drawLine(QPointF(153, 193), QPointF(187, 193))

    painter.end()
    return image


def icon_png_bytes(size: int) -> bytes:
    buffer_bytes = QByteArray()
    buffer = QBuffer(buffer_bytes)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not render_icon_image(size).save(buffer, "PNG"):
        raise RuntimeError(f"Simge PNG olarak oluşturulamadı: {size}x{size}")
    return bytes(buffer_bytes)


def _ico_frame_bytes(size: int) -> bytes:
    if size >= 256:
        return icon_png_bytes(size)
    flipped = render_icon_image(size).convertToFormat(QImage.Format.Format_ARGB32).transformed(QTransform().scale(1, -1))
    xor_bytes = bytes(flipped.constBits())[:size * size * 4]
    and_row_bytes = ((size + 31) // 32) * 4
    and_mask = bytearray(and_row_bytes * size)
    for y in range(size):
        row_offset = y * size * 4
        mask_offset = y * and_row_bytes
        for x in range(size):
            if xor_bytes[row_offset + x * 4 + 3] == 0:
                and_mask[mask_offset + (x >> 3)] |= 0x80 >> (x & 7)
    header = struct.pack("<IIIHHIIIIII", 40, size, size * 2, 1, 32, 0, len(xor_bytes) + len(and_mask), 0, 0, 0, 0)
    return header + xor_bytes + bytes(and_mask)


def write_ico(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    payloads = [(size, _ico_frame_bytes(size)) for size in sizes]
    offset = 6 + 16 * len(payloads)
    entries = []
    for size, data in payloads:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(payloads)) + b"".join(entries) + b"".join(data for _, data in payloads))
    return path


def write_icns(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    types = (
        (16, b"icp4"), (32, b"icp5"), (64, b"icp6"),
        (128, b"ic07"), (256, b"ic08"), (512, b"ic09"), (1024, b"ic10"),
    )
    chunks = []
    for size, ostype in types:
        data = icon_png_bytes(size)
        chunks.append(ostype + struct.pack(">I", 8 + len(data)) + data)
    body = b"".join(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    return path


def build_icon_file(directory: Path, platform_name: str | None = None) -> Path:
    target = platform_name or sys.platform
    directory.mkdir(parents=True, exist_ok=True)
    if target == "win32":
        return write_ico(directory / "YTIndir.ico")
    if target == "darwin":
        return write_icns(directory / "YTIndir.icns")
    path = directory / "YTIndir.png"
    path.write_bytes(icon_png_bytes(256))
    return path


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render_icon_image(size)))
    return icon



STYLE = """
QWidget { font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 13px; color: #e6edf7; }
QMainWindow, QWidget#root { background: #111722; }
QLabel#heading { font-size: 27px; font-weight: 700; }
QLabel#subheading { color: #9baec6; }
QPlainTextEdit, QLineEdit, QComboBox, QTableWidget {
 background: #1a2332; border: 1px solid #344258; border-radius: 7px; padding: 7px;
 selection-background-color: #345b9f;
}
QPushButton { background: #25344b; border: 1px solid #3c506e; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover { background: #324968; }
QPushButton:disabled { color: #66778d; background: #1a2332; border-color: #263448; }
QPushButton#primary { background: #3977e8; border-color: #3977e8; font-weight: 600; }
QPushButton#primary:hover { background: #4d8bff; }
QPushButton#primary:disabled { background: #243b60; color: #7489aa; }
QHeaderView::section { background: #202d41; color: #afc0d7; padding: 10px; border: none; }
QTableWidget { gridline-color: #28364b; alternate-background-color: #1d293b; }
QProgressBar { border: none; background: #25344b; border-radius: 5px; text-align: center; height: 19px; }
QProgressBar::chunk { background: #3977e8; border-radius: 5px; }
"""


PARALLEL_CONNECTIONS = 8
RANGE_CHUNK_SIZE = 4 * 1024 * 1024
MAX_IN_FLIGHT_CHUNKS = 12


def validate_range_response(response, start: int, end: int, total: int | None = None) -> int:
    """Reject ignored ranges, wrong offsets, changed sizes and compressed bodies."""
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
    if response.status_code != 206 or not match:
        raise ValueError("Sunucu istenen HTTP aralığını döndürmedi.")
    first, last, size = map(int, match.groups())
    if (first != start or last != end or size <= end or (total is not None and size != total)
            or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
        raise ValueError("HTTP parça aralığı veya dosya boyutu değişti.")
    length = response.headers.get("Content-Length")
    if length is not None and int(length) != end - start + 1:
        raise ValueError("HTTP parça uzunluğu geçersiz.")
    return size


class FastDownloadProcess(QObject):
    """QProcess-compatible pipeline: 8x parallel HTTP range download + on-the-fly H.264 transcoding + fallbacks."""

    readyReadStandardOutput = Signal()
    finished = Signal(int, object)
    errorOccurred = Signal(object)

    def __init__(self, job: Job, engine: Path, args: list[str], ffmpeg: Path | None,
                 ffprobe: Path | None, deno: Path | None, workspace: Path, parent=None):
        super().__init__(parent)
        self.job = job
        self.engine = engine
        self.args = list(args)
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.deno = deno
        self.workspace = workspace
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._children: list[subprocess.Popen] = []
        self._cancelled = False
        self._cancel_event = threading.Event()
        self._running = False
        self._error_string = ""
        self._captured_file = ""
        self._thread = QThread(self)
        self._thread.run = self._run_pipeline
        self._thread.finished.connect(self._on_thread_finished)
        self._exit_code = 1

    def start(self):
        if not self.engine or not self.engine.is_file():
            self._error_string = f"İndirme motoru bulunamadı: {self.engine}"
            QTimer.singleShot(0, lambda: self.errorOccurred.emit(QProcess.ProcessError.FailedToStart))
            return
        self._running = True
        self._thread.start()

    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running if self._running or self._thread.isRunning() else QProcess.ProcessState.NotRunning

    def processId(self) -> int:
        return 0

    def errorString(self) -> str:
        return self._error_string or "İşlem başlatılamadı."

    def readAllStandardOutput(self) -> bytes:
        with self._buffer_lock:
            data = bytes(self._buffer)
            self._buffer.clear()
            return data

    def _emit_line(self, line: str):
        if line.startswith("__FILE__"):
            try:
                self._captured_file = json.loads(line[len("__FILE__"):])
            except ValueError:
                pass
        with self._buffer_lock:
            self._buffer.extend((line + "\n").encode("utf-8", "replace"))
        self.readyReadStandardOutput.emit()

    def _spawn_child(self, cmd: list[str], *, stdin=subprocess.DEVNULL) -> subprocess.Popen:
        kwargs = {
            "stdin": stdin,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "env": child_environment(),
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        with self._proc_lock:
            if self._cancelled:
                raise InterruptedError("Cancelled")
            proc = subprocess.Popen(cmd, **kwargs)
            self._children.append(proc)
            return proc

    def _kill_child(self, proc: subprocess.Popen):
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.kill()
        except OSError:
            pass

    def kill(self):
        self._cancelled = True
        self._cancel_event.set()
        with self._proc_lock:
            children = list(self._children)
        for proc in children:
            self._kill_child(proc)

    def waitForFinished(self, msecs: int = 30000) -> bool:
        return self._thread.wait(msecs)

    def _on_thread_finished(self):
        self._running = False
        self.finished.emit(self._exit_code, QProcess.ExitStatus.NormalExit)

    def _run_pipeline(self):
        try:
            self._run_download()
        except Exception as error:
            self._exit_code = 1
            if not self._cancelled:
                self._emit_line(f"İndirme tamamlanamadı: {error}")
        finally:
            self._stop_children()

    def _stop_children(self):
        with self._proc_lock:
            children = list(self._children)
        for proc in children:
            self._kill_child(proc)
            proc.wait()

    def _run_download(self):
        try:
            if self._try_parallel_pipeline():
                self._exit_code = 0
                return
        except InterruptedError:
            self._exit_code = 1
            return
        except Exception as error:
            if self._cancelled:
                self._exit_code = 1
                return
            self._emit_line(f"Hızlı akış yedek motora geçiyor: {error}")

        if not self._cancelled:
            # A failed streaming encoder must not survive into the fallback.
            self._stop_children()
            self._exit_code = self._run_ytdlp_fallback()

    def _extract_info(self) -> dict | None:
        cmd = [
            str(self.engine), "--ignore-config", "--no-playlist", "--no-warnings",
            "--encoding", "utf-8", "--socket-timeout", "15", "--retries", "2",
            "--windows-filenames", "-P", str(self.workspace),
        ]
        if self.ffmpeg:
            cmd += ["--ffmpeg-location", str(self.ffmpeg.parent)]
        if self.deno:
            cmd += ["--no-js-runtimes", "--js-runtimes", f"deno:{self.deno}"]
        for flag in ("-o", "-f", "-S"):
            if flag in self.args:
                idx = self.args.index(flag)
                if idx + 1 < len(self.args):
                    cmd += [flag, self.args[idx + 1]]
        cmd += ["--dump-single-json", "--", self.job.url]
        proc = self._spawn_child(cmd)
        out, _ = proc.communicate()
        if self._cancelled:
            raise InterruptedError("Cancelled")
        if proc.returncode != 0 or not out:
            return None
        lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None

    def _probe_stream_range(self, session: requests.Session, stream: dict) -> int | None:
        url = stream.get("url") or ""
        protocol = (stream.get("protocol") or "").lower()
        if not url.startswith(("http://", "https://")) or protocol not in ("https", "http") or stream.get("fragments"):
            return None
        headers = dict(stream.get("http_headers") or {})
        headers["Range"] = "bytes=0-0"
        headers["Accept-Encoding"] = "identity"
        try:
            with session.get(url, headers=headers, stream=True, timeout=(10, 15)) as resp:
                return validate_range_response(resp, 0, 0)
        except (requests.RequestException, ValueError):
            return None

    def _parallel_download_file(self, url: str, headers: dict, total_size: int, dest_path: Path,
                                offset_bytes: int, total_all_bytes: int,
                                pipe_stdin=None, *, stream_id: str | None = None) -> tuple[bool, bool]:
        """Download via 8 concurrent HTTP range workers, writing ordered chunks to disk and optional FFmpeg pipe."""
        num_chunks = max(1, (total_size + RANGE_CHUNK_SIZE - 1) // RANGE_CHUNK_SIZE)
        # Signed URLs change on reconnect. Format identity and size must match
        # before reusing the contiguous, validated prefix already on disk.
        resume_path = dest_path.with_name(dest_path.name + ".resume.json")
        identity = {"video": self.job.url, "stream": stream_id or url, "size": total_size}
        resume_bytes = 0
        try:
            if json.loads(resume_path.read_text(encoding="utf-8")) == identity:
                size = dest_path.stat().st_size
                if size <= total_size:
                    resume_bytes = size if size == total_size else size // RANGE_CHUNK_SIZE * RANGE_CHUNK_SIZE
        except (OSError, ValueError):
            pass
        live_lock = threading.Lock()
        live_bytes = [resume_bytes]
        speed_window: deque[tuple[float, int]] = deque()
        thread_local = threading.local()
        sessions: list[requests.Session] = []

        def get_session() -> requests.Session:
            sess = getattr(thread_local, "session", None)
            if sess is None:
                sess = requests.Session()
                sess.verify = certifi.where()
                adapter = HTTPAdapter(pool_connections=PARALLEL_CONNECTIONS, pool_maxsize=PARALLEL_CONNECTIONS)
                sess.mount("https://", adapter)
                sess.mount("http://", adapter)
                thread_local.session = sess
                with live_lock:
                    sessions.append(sess)
            return sess

        def fetch_chunk(index: int) -> bytes:
            start = index * RANGE_CHUNK_SIZE
            end = min(total_size - 1, (index + 1) * RANGE_CHUNK_SIZE - 1)
            expected = end - start + 1
            req_headers = dict(headers)
            req_headers["Range"] = f"bytes={start}-{end}"
            req_headers["Accept-Encoding"] = "identity"
            for attempt in range(4):
                if self._cancelled:
                    raise InterruptedError("Cancelled")
                added = 0
                try:
                    with get_session().get(url, headers=req_headers, stream=True, timeout=(10, 20)) as resp:
                        validate_range_response(resp, start, end, total_size)
                        buf = bytearray()
                        for block in resp.iter_content(64 * 1024):
                            if self._cancelled:
                                raise InterruptedError("Cancelled")
                            if not block:
                                continue
                            if len(buf) + len(block) > expected:
                                raise ValueError("HTTP parçası beklenen boyutu aşıyor.")
                            buf.extend(block)
                            added += len(block)
                            with live_lock:
                                live_bytes[0] += len(block)
                        if len(buf) == expected:
                            return bytes(buf)
                        raise RuntimeError("Eksik parça boyutu")
                except InterruptedError:
                    raise
                except Exception:
                    with live_lock:
                        live_bytes[0] = max(0, live_bytes[0] - added)
                    if attempt == 3:
                        raise
                    if self._cancel_event.wait(0.4 * (2 ** attempt)):
                        raise InterruptedError("Cancelled")
            raise RuntimeError("Parça indirilemedi")

        def emit_progress(finished: bool = False):
            now = time.monotonic()
            with live_lock:
                current_dl = min(total_all_bytes, offset_bytes + live_bytes[0])
            speed_window.append((now, current_dl))
            while len(speed_window) > 1 and now - speed_window[0][0] > 3.0:
                speed_window.popleft()
            dt = now - speed_window[0][0] if len(speed_window) > 1 else 0.0
            speed = (current_dl - speed_window[0][1]) / dt if dt >= 0.15 else None
            remaining = max(0, total_all_bytes - current_dl)
            eta = int(remaining / speed) if speed and speed > 0 else None
            payload = {
                "filename": "download",
                "status": "finished" if finished else "downloading",
                "downloaded_bytes": total_all_bytes if finished else current_dl,
                "total_bytes": total_all_bytes,
                "speed": speed,
                "eta": 0 if finished else eta,
            }
            self._emit_line("__PROGRESS__" + json.dumps(payload))

        pipe_ok = pipe_stdin is not None
        def write_pipe(data: bytes):
            nonlocal pipe_ok
            if self._cancelled:
                raise InterruptedError("Cancelled")
            if pipe_ok:
                try:
                    pipe_stdin.write(data)
                    pipe_stdin.flush()
                except (BrokenPipeError, OSError):
                    pipe_ok = False
                    try:
                        pipe_stdin.close()
                    except OSError:
                        pass

        in_flight: dict[int, concurrent.futures.Future[bytes]] = {}
        first_chunk = (resume_bytes + RANGE_CHUNK_SIZE - 1) // RANGE_CHUNK_SIZE
        next_submit = first_chunk
        last_report = 0.0

        with ExitStack() as stack:
            # Close per-worker sessions after the executor has joined its workers.
            def close_sessions():
                for session in sessions:
                    session.close()
            stack.callback(close_sessions)
            out_file = stack.enter_context(dest_path.open("r+b" if dest_path.exists() else "w+b"))
            out_file.truncate(resume_bytes)
            resume_path.write_text(json.dumps(identity), encoding="utf-8")
            # FFmpeg's new pipe needs the existing prefix too, even though the
            # network only needs to fetch the remaining ranges.
            remaining_prefix = resume_bytes
            while pipe_ok and remaining_prefix:
                data = out_file.read(min(64 * 1024, remaining_prefix))
                if not data:
                    raise ValueError("Devam dosyası beklenenden kısa.")
                write_pipe(data)
                remaining_prefix -= len(data)
            out_file.seek(resume_bytes)
            pool = stack.enter_context(concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_CONNECTIONS))
            try:
                for next_write in range(first_chunk, num_chunks):
                    while (next_submit < num_chunks and len(in_flight) < PARALLEL_CONNECTIONS
                           and (next_submit - next_write) < MAX_IN_FLIGHT_CHUNKS):
                        in_flight[next_submit] = pool.submit(fetch_chunk, next_submit)
                        next_submit += 1

                    fut = in_flight[next_write]
                    while True:
                        if self._cancelled:
                            raise InterruptedError("Cancelled")
                        try:
                            chunk_data = fut.result(timeout=0.2)
                            break
                        except concurrent.futures.TimeoutError:
                            now = time.monotonic()
                            if now - last_report >= 0.2:
                                emit_progress(False)
                                last_report = now

                    del in_flight[next_write]
                    while (next_submit < num_chunks and len(in_flight) < PARALLEL_CONNECTIONS
                           and (next_submit - next_write) < MAX_IN_FLIGHT_CHUNKS):
                        in_flight[next_submit] = pool.submit(fetch_chunk, next_submit)
                        next_submit += 1

                    out_file.write(chunk_data)
                    out_file.flush()
                    write_pipe(chunk_data)

                    now = time.monotonic()
                    if now - last_report >= 0.2:
                        emit_progress(False)
                        last_report = now
            finally:
                for fut in in_flight.values():
                    fut.cancel()

        return True, pipe_ok

    def _run_ffmpeg_with_progress(self, cmd: list[str], *, forward_progress: bool = True) -> int:
        proc = self._spawn_child(cmd)
        assert proc.stdout is not None
        for raw_line in iter(proc.stdout.readline, b""):
            if self._cancelled:
                self._kill_child(proc)
                raise InterruptedError("Cancelled")
            line = raw_line.decode("utf-8", "replace").strip()
            if line and forward_progress:
                self._emit_line(line)
        return proc.wait()

    def _transcode_local_mp4(self, video_path: Path, audio_path: Path | None,
                             output_path: Path, *, copy_audio: bool) -> bool:
        """Transcode local file to Adobe-compatible H.264 + AAC, trying GPU first then universal CPU fallback."""
        if not self.ffmpeg:
            return False
        self._emit_line("__POST__{}")
        encoders = available_h264_encoders(self.ffmpeg)
        for name, enc_args in encoders:
            if self._cancelled:
                raise InterruptedError("Cancelled")
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            cmd = [str(self.ffmpeg), *transcode_arguments(
                str(video_path), str(audio_path) if audio_path else None,
                output_path, enc_args, copy_audio=copy_audio,
            )]
            code = self._run_ffmpeg_with_progress(cmd, forward_progress=True)
            if code == 0 and output_path.is_file() and output_path.stat().st_size > 0:
                return True
            self._emit_line(f"Donanım kodlayıcı ({name}) başarısız oldu; uyumlu CPU kodlayıcıya geçiliyor…")
        return False

    def _try_parallel_pipeline(self) -> bool:
        if not self.ffmpeg or not self.ffmpeg.is_file():
            return False
        info = self._extract_info()
        if not info or info.get("is_live"):
            return False

        title = info.get("title") or self.job.url
        self._emit_line("__TITLE__" + json.dumps(title))
        duration = info.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            self._emit_line("__DURATION__" + json.dumps(duration))

        raw_formats = info.get("requested_formats") or [info]
        session = requests.Session()
        session.verify = certifi.where()
        try:
            stream_sizes: list[int] = []
            for fmt in raw_formats:
                size = self._probe_stream_range(session, fmt)
                if not size:
                    return False
                stream_sizes.append(size)
        finally:
            session.close()

        total_all_bytes = sum(stream_sizes)
        self._emit_line('__STREAMS__["download"]')

        target_stem = video_output_stem(self.job, str(info.get("title") or "YouTube video"),
                                        str(info.get("id") or self.job.id))
        final_ext = "." + self.job.format.lower()
        final_path = self.workspace / (target_stem + final_ext)

        if self.job.format == "MP3":
            fmt = raw_formats[0]
            ext = "." + (fmt.get("ext") or "m4a").lstrip(".")
            raw_audio = self.workspace / (f"{target_stem}{ext}" if self.job.start_time is not None
                                          else f"raw_audio{ext}")
            if self.job.start_time is None:
                # On-the-fly MP3 encoding while parallel-downloading audio chunks.
                mp3_cmd = [
                    str(self.ffmpeg), "-hide_banner", "-y", "-i", "pipe:0",
                    "-vn", "-c:a", "libmp3lame", "-b:a", f"{int(self.job.quality)}k",
                    "-progress", "pipe:1", "-nostats", str(final_path),
                ]
                ff_proc = self._spawn_child(mp3_cmd, stdin=subprocess.PIPE)
                download_done = threading.Event()

                def drain_mp3():
                    assert ff_proc.stdout is not None
                    for raw in iter(ff_proc.stdout.readline, b""):
                        if download_done.is_set():
                            line = raw.decode("utf-8", "replace").strip()
                            if line:
                                self._emit_line(line)

                reader = threading.Thread(target=drain_mp3, daemon=True)
                reader.start()
                _, pipe_ok = self._parallel_download_file(
                    fmt["url"], fmt.get("http_headers") or {}, stream_sizes[0],
                    raw_audio, 0, total_all_bytes, pipe_stdin=ff_proc.stdin,
                    stream_id=fmt.get("format_id"),
                )
                download_done.set()
                self._emit_line('__PROGRESS__{"filename":"download","status":"finished","downloaded_bytes":'
                                + f'{total_all_bytes},"total_bytes":{total_all_bytes}}}')
                if ff_proc.stdin:
                    try:
                        ff_proc.stdin.close()
                    except OSError:
                        pass
                self._emit_line("__POST__{}")
                code = ff_proc.wait()
                reader.join(timeout=2.0)
                if not pipe_ok or code != 0 or not final_path.is_file() or final_path.stat().st_size == 0:
                    fallback_cmd = [
                        str(self.ffmpeg), "-hide_banner", "-y", "-i", str(raw_audio),
                        "-vn", "-c:a", "libmp3lame", "-b:a", f"{int(self.job.quality)}k",
                        "-progress", "pipe:1", "-nostats", str(final_path),
                    ]
                    if self._run_ffmpeg_with_progress(fallback_cmd) != 0:
                        return False
                try:
                    raw_audio.unlink(missing_ok=True)
                except OSError:
                    pass
                self._emit_line("__FILE__" + json.dumps(str(final_path)))
                return True
            else:
                self._parallel_download_file(
                    fmt["url"], fmt.get("http_headers") or {}, stream_sizes[0],
                    raw_audio, 0, total_all_bytes,
                    stream_id=fmt.get("format_id"),
                )
                self._emit_line('__PROGRESS__{"filename":"download","status":"finished","downloaded_bytes":'
                                + f'{total_all_bytes},"total_bytes":{total_all_bytes}}}')
                self._emit_line("__FILE__" + json.dumps(str(raw_audio)))
                return True

        # MP4 pipeline
        video_fmt = raw_formats[0]
        video_size = stream_sizes[0]
        audio_fmt = raw_formats[1] if len(raw_formats) > 1 else None
        audio_size = stream_sizes[1] if len(raw_formats) > 1 else 0

        vcodec = (video_fmt.get("vcodec") or "").lower()
        acodec = ((audio_fmt or video_fmt).get("acodec") or "").lower()
        is_h264 = vcodec.startswith(("avc1", "h264"))
        is_aac = acodec.startswith(("mp4a", "aac"))

        raw_audio: Path | None = None
        offset = 0
        if audio_fmt is not None:
            a_ext = "." + (audio_fmt.get("ext") or "m4a").lstrip(".")
            raw_audio = self.workspace / f"raw_audio{a_ext}"
            self._parallel_download_file(
                audio_fmt["url"], audio_fmt.get("http_headers") or {}, audio_size,
                raw_audio, 0, total_all_bytes,
                stream_id=audio_fmt.get("format_id"),
            )
            offset = audio_size

        v_ext = "." + (video_fmt.get("ext") or "webm").lstrip(".")
        raw_video = self.workspace / f"raw_video{v_ext}"

        if not is_h264 and self.job.start_time is None:
            # >1080p VP9/AV1 full video: transcode on the fly while parallel-downloading video chunks!
            encoders = available_h264_encoders(self.ffmpeg)
            enc_name, enc_args = encoders[0]
            self._emit_line(f"Adobe uyumlu H.264 anlık dönüştürme başlatıldı ({enc_name}).")
            fly_cmd = [str(self.ffmpeg), *transcode_arguments(
                "pipe:0", str(raw_audio) if raw_audio else None,
                final_path, enc_args, copy_audio=is_aac,
            )]
            ff_proc = self._spawn_child(fly_cmd, stdin=subprocess.PIPE)
            download_done = threading.Event()

            def drain_video():
                assert ff_proc.stdout is not None
                for raw in iter(ff_proc.stdout.readline, b""):
                    if download_done.is_set():
                        line = raw.decode("utf-8", "replace").strip()
                        if line:
                            self._emit_line(line)

            reader = threading.Thread(target=drain_video, daemon=True)
            reader.start()
            _, pipe_ok = self._parallel_download_file(
                video_fmt["url"], video_fmt.get("http_headers") or {}, video_size,
                raw_video, offset, total_all_bytes, pipe_stdin=ff_proc.stdin,
                stream_id=video_fmt.get("format_id"),
            )
            download_done.set()
            self._emit_line('__PROGRESS__{"filename":"download","status":"finished","downloaded_bytes":'
                            + f'{total_all_bytes},"total_bytes":{total_all_bytes}}}')
            if ff_proc.stdin:
                try:
                    ff_proc.stdin.close()
                except OSError:
                    pass
            self._emit_line("__POST__{}")
            code = ff_proc.wait()
            reader.join(timeout=2.0)

            if not pipe_ok or code != 0 or not final_path.is_file() or final_path.stat().st_size == 0:
                if not self._transcode_local_mp4(raw_video, raw_audio, final_path, copy_audio=is_aac):
                    return False

            for temp_file in (raw_video, raw_audio):
                if temp_file:
                    try:
                        temp_file.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._emit_line("__FILE__" + json.dumps(str(final_path)))
            return True

        # Either already H.264 (<=1080p) or a section clip (which will be cut/transcoded locally)
        self._parallel_download_file(
            video_fmt["url"], video_fmt.get("http_headers") or {}, video_size,
            raw_video, offset, total_all_bytes,
            stream_id=video_fmt.get("format_id"),
        )
        self._emit_line('__PROGRESS__{"filename":"download","status":"finished","downloaded_bytes":'
                        + f'{total_all_bytes},"total_bytes":{total_all_bytes}}}')
        self._emit_line("__POST__{}")

        if is_h264:
            a_args = ["-c:a", "copy"] if is_aac else ["-c:a", "aac", "-b:a", "192k"]
            mux_cmd = [str(self.ffmpeg), "-hide_banner", "-y", "-i", str(raw_video)]
            if raw_audio:
                mux_cmd += ["-i", str(raw_audio), "-map", "0:v:0", "-map", "1:a:0"]
            else:
                mux_cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            mux_cmd += ["-c:v", "copy", *a_args, "-movflags", "+faststart", str(final_path)]
            if self._run_ffmpeg_with_progress(mux_cmd, forward_progress=False) != 0:
                return False
            out_target = final_path
        else:
            # Section clip of VP9/AV1 (>1080p): fast-mux into temporary container so start_local_cut
            # only transcodes the requested clip slice to H.264 instead of the entire video.
            source_mkv = self.workspace / f"{target_stem}.mkv"
            mux_cmd = [str(self.ffmpeg), "-hide_banner", "-y", "-i", str(raw_video)]
            if raw_audio:
                mux_cmd += ["-i", str(raw_audio), "-map", "0:v:0", "-map", "1:a:0"]
            else:
                mux_cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            mux_cmd += ["-c", "copy", str(source_mkv)]
            if self._run_ffmpeg_with_progress(mux_cmd, forward_progress=False) != 0:
                return False
            out_target = source_mkv

        for temp_file in (raw_video, raw_audio):
            if temp_file:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass
        self._emit_line("__FILE__" + json.dumps(str(out_target)))
        return True

    def _run_ytdlp_fallback(self) -> int:
        try:
            proc = self._spawn_child([str(self.engine), *self.args])
        except InterruptedError:
            return 1
        assert proc.stdout is not None
        for raw_line in iter(proc.stdout.readline, b""):
            if self._cancelled:
                self._kill_child(proc)
                return 1
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if line:
                self._emit_line(line)
        code = proc.wait()
        if code != 0 or self._cancelled:
            return code
        if self.job.format == "MP4" and self.job.start_time is None and self._captured_file and self.ffmpeg:
            out_path = Path(self._captured_file)
            vcodec, acodec = probe_media_codecs(self.ffprobe, out_path)
            if vcodec and (vcodec != "h264" or (acodec and acodec != "aac")):
                transcoded = out_path.with_name(out_path.stem + ".h264.mp4")
                if self._transcode_local_mp4(out_path, None, transcoded, copy_audio=(acodec == "aac")):
                    os.replace(transcoded, out_path)
                    self._emit_line("__FILE__" + json.dumps(str(out_path)))
                else:
                    return 1
        return 0


class Window(QMainWindow):
    def __init__(self, data_dir: Path, *, auto_update: bool = True):
        super().__init__()
        self.data_dir = data_dir
        self.queue_file = data_dir / "queue.json"
        self.settings = StateManager(data_dir)
        self.downloads_dir = data_dir / "downloads"
        cleanup_errors = cleanup_downloads(self.downloads_dir)
        self.jobs: list[Job] = []
        self.queue_error = ""
        try:
            self.jobs = load_jobs(self.queue_file)
        except (OSError, ValueError, TypeError) as error:
            self.queue_error = str(error)
        self.engine: Path | None = None
        self.deno: Path | None = None
        self.ffmpeg: Path | None = None
        self.ffprobe: Path | None = None
        self.updater: UpdateThread | None = None
        self.process: QProcess | None = None
        self.probe: QProcess | None = None
        self.current: Job | None = None
        self.running = False
        self.closing = False
        self.stop_after = False
        self.cancelled = False
        self.pending_start = False
        self.last_check = 0.0
        self.allow_old_for_batch = False
        self.output_buffer = ""
        self.clip_progress: dict[str, str] = {}
        self.download_streams: dict[str, float] = {}
        self.download_bytes: dict[str, int] = {}
        self.source_duration: float | None = None
        self.stream_count = 1
        self.converting = False
        self.conversion_started = 0.0
        self.conversion_estimate = 0.0
        self.conversion_timer = QTimer(self)
        self.conversion_timer.setInterval(200)
        self.conversion_timer.timeout.connect(self.update_conversion_progress)
        self.update_label = "Güncellemeler denetleniyor…"
        self.clip_workspace: tempfile.TemporaryDirectory | None = None
        self.clip_destination: Path | None = None
        self.cutting = False
        self.cut_fallback_args: list[str] | None = None
        self.log_lines: list[str] = []
        self.setWindowTitle("YT İndir · MP3 ve MP4")
        self.setWindowIcon(app_icon())
        self.resize(760, 620)
        self.setMinimumSize(640, 540)
        self.build_ui()
        for error in cleanup_errors:
            self.log(f"Geçici indirme dosyaları temizlenemedi: {error}")
        self.refresh_table()
        self.quality.setCurrentIndex(max(0, self.quality.findData(self.settings.value("quality", "128" if self.format.currentText() == "MP3" else "best"))))
        if auto_update:
            QTimer.singleShot(0, self.check_updates)

    def build_ui(self):
        root = QWidget(objectName="root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_label.setPixmap(QPixmap.fromImage(render_icon_image(36)))
        icon_label.setFixedSize(36, 36)
        header.addWidget(icon_label)
        header.addWidget(QLabel("YT İndir", objectName="heading"))
        header.addStretch()
        more = QPushButton("Diğer ⋯")
        menu = QMenu(more)
        menu.addAction("İndirilen klasörü aç", self.open_folder)
        menu.addAction("Başarısız indirmeleri yeniden sırala", self.retry_failed)
        menu.addAction("Tamamlananları temizle", self.clear_finished)
        menu.addSeparator()
        self.update_button = menu.addAction("Güncellemeleri denetle", self.check_updates)
        menu.addAction("Yeni uygulama sürümleri", lambda: QDesktopServices.openUrl(
            QUrl(f"https://github.com/{APP_REPOSITORY}/releases")))
        details_action = menu.addAction("İşlem ayrıntılarını göster")
        details_action.setCheckable(True)
        more.setMenu(menu)
        header.addWidget(more)
        layout.addLayout(header)
        layout.addWidget(QLabel("Bağlantıyı yapıştırın, biçimi seçin ve indirin.", objectName="subheading"))
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("YouTube bağlantısını buraya yapıştırın\nBirden fazla video için her satıra bir bağlantı ekleyin.")
        self.links.setFixedHeight(94)
        self.links.setAccessibleName("YouTube bağlantıları")
        layout.addWidget(self.links)

        self.format = QComboBox()
        self.format.addItems(["MP4", "MP3"])
        self.format.setCurrentText("MP3")
        self.format.hide()
        formats = QHBoxLayout()
        self.video_button = QPushButton("Video · MP4")
        self.audio_button = QPushButton("Yalnızca ses · MP3")
        for button, value in ((self.video_button, "MP4"), (self.audio_button, "MP3")):
            button.setCheckable(True)
            button.setMinimumHeight(46)
            button.clicked.connect(lambda checked=False, value=value: self.select_format(value))
            formats.addWidget(button)
        layout.addLayout(formats)
        self.quality = QComboBox()
        self.quality.setObjectName("downloadQuality")
        self.quality.setView(QListView())
        self.quality.setStyleSheet("""
QComboBox#downloadQuality QAbstractItemView {
    color: black; background-color: white;
    selection-color: black; selection-background-color: #c9dfff;
}
""")
        self.quality.setMinimumHeight(36)
        self.format.currentTextChanged.connect(self.update_qualities)
        self.update_qualities()
        self.select_format(self.format.currentText())

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Kayıt yeri", objectName="subheading"))
        self.folder = QLineEdit(str(self.settings.value("folder", str(Path.home() / "Downloads" / "YT İndir"))))
        self.folder.setReadOnly(True)
        self.folder.setAccessibleName("Kayıt klasörü")
        folder_row.addWidget(self.folder, 1)
        browse = QPushButton("Değiştir")
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        self.clip_enabled = QCheckBox("Yalnızca belirli bir bölümü indir")
        layout.addWidget(self.clip_enabled)
        self.clip_options = QWidget()
        clip_layout = QVBoxLayout(self.clip_options)
        clip_layout.setContentsMargins(0, 0, 0, 0)
        times = QHBoxLayout()
        times.addWidget(QLabel("Başlangıç"))
        self.clip_start = QLineEdit("00:00")
        self.clip_start.setAccessibleName("Kesit başlangıcı")
        times.addWidget(self.clip_start, 1)
        times.addWidget(QLabel("Bitiş"))
        self.clip_end = QLineEdit()
        self.clip_end.setPlaceholderText("Örnek: 01:30")
        self.clip_end.setAccessibleName("Kesit bitişi")
        times.addWidget(self.clip_end, 1)
        clip_layout.addLayout(times)
        clip_help = QLabel("Örnek: 01:00 → 01:30. Kaynağın tamamı indirilir, sonra bu 30 saniye kesilir.", objectName="subheading")
        clip_help.setText(clip_help.text() + " Hızlı video kesiminde kesim noktaları tam zamanına uymayabilir.")
        clip_help.setWordWrap(True)
        clip_layout.addWidget(clip_help)
        self.clip_options.hide()
        self.clip_enabled.toggled.connect(self.clip_options.setVisible)
        layout.addWidget(self.clip_options)

        options_button = QPushButton("Kalite ve ayarlar ▸")
        options_button.setCheckable(True)
        layout.addWidget(options_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.options = QWidget()
        options_layout = QVBoxLayout(self.options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("İndirme kalitesi"))
        quality_row.addWidget(self.quality, 1)
        options_layout.addLayout(quality_row)
        self.engine_status = QLabel("Gerekli güncellemeler otomatik denetlenir.", objectName="subheading")
        self.engine_status.setWordWrap(True)
        options_layout.addWidget(self.engine_status)
        self.options.hide()
        options_button.toggled.connect(self.options.setVisible)
        options_button.toggled.connect(lambda checked: options_button.setText(
            "Kalite ve ayarlar ▾" if checked else "Kalite ve ayarlar ▸"))
        layout.addWidget(self.options)

        actions = QHBoxLayout()
        self.start = QPushButton("İndir", objectName="primary")
        self.start.setMinimumHeight(48)
        self.start.clicked.connect(self.start_batch)
        actions.addWidget(self.start, 1)
        self.add_button = QPushButton("Sıraya ekle")
        self.add_button.clicked.connect(self.add_links)
        actions.addWidget(self.add_button)
        self.stop = QPushButton("Bu video bitince dur")
        self.stop.clicked.connect(self.request_stop)
        actions.addWidget(self.stop)
        self.cancel = QPushButton("İptal et")
        self.cancel.clicked.connect(self.cancel_current)
        actions.addWidget(self.cancel)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress_animation = QPropertyAnimation(self.progress, b"value", self)
        self.progress_animation.setDuration(250)
        self.progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("Hazır. İlk bağlantınızı yukarıya yapıştırın.", objectName="subheading")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.queue_label = QLabel("İndirmeler", objectName="subheading")
        layout.addWidget(self.queue_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Başlık", "Biçim", "Durum", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(130)
        self.table.itemDoubleClicked.connect(self.show_job)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(500)
        self.details.setMaximumHeight(110)
        self.details.hide()
        details_action.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        self.setStyleSheet(STYLE + """
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #ffffff; border-radius: 3px; }
QCheckBox::indicator:unchecked { background: #ffffff; }
QCheckBox::indicator:checked { background: #3977e8; }
QCheckBox::indicator:hover { border-color: #9cc0ff; }
QPushButton:checked { background: #243f65; border: 2px solid #6b9df7; color: #ffffff; }
QMenu { background: #1a2332; color: #e6edf7; border: 1px solid #344258; padding: 6px; }
QMenu::item { padding: 9px 18px; }
QMenu::item:selected { background: #345b9f; }
QLineEdit:read-only { background: transparent; border: none; color: #afc0d7; }
QScrollArea { background: #111722; }
QScrollBar:vertical { background: #111722; width: 10px; }
QScrollBar::handle:vertical { background: #344258; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
""")
        self.sync_buttons()

    def select_format(self, value):
        self.format.setCurrentText(value)
        self.video_button.setChecked(value == "MP4")
        self.audio_button.setChecked(value == "MP3")

    def update_qualities(self):
        self.quality.clear()
        if self.format.currentText() == "MP3":
            for value in (128, 192, 256, 320):
                self.quality.addItem(f"{value} kbps", str(value))
        else:
            self.quality.addItem("En iyi çözünürlük", "best")
            for value in (2160, 1440, 1080, 720, 480):
                self.quality.addItem(f"En fazla {value}p", str(value))

    def sync_buttons(self):
        updating = self.updater is not None
        self.start.setEnabled(not self.running and not updating)
        self.stop.setEnabled(self.running)
        self.cancel.setEnabled(self.current is not None and not self.cancelled)
        self.update_button.setEnabled(not self.running and not updating)
        self.start.setText(self.update_label if updating else "İndiriliyor…" if self.running else "İndir")
        self.stop.setVisible(self.running)
        self.cancel.setVisible(self.current is not None)
        self.add_button.setVisible(self.running)
        self.progress.setVisible(self.running or self.pending_start)

    def log(self, text: str):
        self.details.appendPlainText(text)

    def choose_folder(self):
        if path := QFileDialog.getExistingDirectory(self, "Kayıt klasörü seçin", self.folder.text()):
            self.folder.setText(path)

    def open_folder(self):
        rows = self.table.selectionModel().selectedRows()
        folder = self.jobs[rows[0].row()].folder if rows else self.folder.text()
        path = Path(folder).expanduser()
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        else:
            QMessageBox.information(self, "Kayıt klasörü", "Klasör, indirme başladığında oluşturulacak.")

    def persist(self):
        try:
            save_jobs(self.queue_file, self.jobs)
        except OSError as error:
            self.status.setText(f"Kuyruk kaydedilemedi: {error}")

    def refresh_table(self):
        self.queue_label.setVisible(bool(self.jobs))
        self.table.setVisible(bool(self.jobs))
        self.queue_label.setText(f"İndirmeler · {len(self.jobs)} video")
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            quality = "En iyi" if job.quality == "best" else job.quality + (" kbps" if job.format == "MP3" else "p")
            title = job.url if job.title == "Bağlantı bilgisi bekleniyor" else job.title
            if job.start_time is not None:
                title += f" [{timestamp_seconds(job.start_time)}–{timestamp_seconds(job.end_time)} sn]"
            state = f"{job.status} · %{job.progress}" if job.status in ("İndiriliyor", "Dönüştürülüyor") else job.status
            for col, value in enumerate((title, f"{job.format} · {quality}", state)):
                item = QTableWidgetItem(value)
                item.setToolTip(job.error or job.output or job.url)
                self.table.setItem(row, col, item)
            remove = QPushButton("Kaldır")
            remove.setAccessibleName(f"Listeden kaldır: {title}")
            busy = job is self.current or job.status in ACTIVE_STATUSES
            remove.setEnabled(not busy)
            remove.setToolTip("İşlem sürerken kaldırılamaz." if busy else "Listeden kaldır; indirilen dosya silinmez.")
            remove.clicked.connect(lambda checked=False, job_id=job.id: self.remove_job(job_id))
            self.table.setCellWidget(row, 3, remove)

    def add_links(self):
        try:
            links = parse_links(self.links.toPlainText())
            start_time, end_time = (clip_bounds(self.clip_start.text(), self.clip_end.text())
                                    if self.clip_enabled.isChecked() else (None, None))
            folder = str(Path(self.folder.text().strip()).expanduser().resolve())
            if not self.folder.text().strip():
                raise ValueError("Bir kayıt klasörü seçin.")
        except ValueError as error:
            QMessageBox.warning(self, "Bağlantıları kontrol edin", str(error))
            return False
        for url in links:
            self.jobs.append(Job(url, self.format.currentText(), self.quality.currentData(), folder,
                                 start_time=start_time, end_time=end_time))
        self.links.clear()
        self.settings.setValue("folder", folder)
        self.settings.setValue("format", self.format.currentText())
        self.settings.setValue("quality", self.quality.currentData())
        self.persist()
        self.refresh_table()
        self.status.setText(f"{len(links)} bağlantı sıraya eklendi.")
        return True

    def remove_job(self, job_id: str):
        job = next((job for job in self.jobs if job.id == job_id), None)
        if job is None or job is self.current or job.status in ACTIVE_STATUSES:
            return
        self.jobs.remove(job)
        self.persist()
        self.refresh_table()

    def retry_failed(self):
        for job in self.jobs:
            if job is not self.current and job.status in ("Başarısız", "İptal edildi"):
                job.status, job.error, job.progress = "Bekliyor", "", 0
        self.persist()
        self.refresh_table()

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.status != "Tamamlandı"]
        self.persist()
        self.refresh_table()

    def show_job(self, item):
        job = self.jobs[item.row()]
        if job.error:
            box = QMessageBox(QMessageBox.Icon.Warning, "İndirme ayrıntıları",
                              "İndirme tamamlanamadı. Bağlantıyı ve internet bağlantınızı kontrol edin.", parent=self)
            box.setDetailedText(job.error)
            box.exec()
        elif job.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(job.output).parent)))

    def check_updates(self):
        if self.updater is not None or self.current:
            return
        self.updater = UpdateThread(self.data_dir, self)
        self.update_label = "Güncellemeler denetleniyor…"
        self.updater.message.connect(self.update_message)
        self.updater.ready.connect(self.update_ready)
        self.updater.failed.connect(self.update_failed)
        self.updater.finished.connect(self.update_finished)
        self.updater.start()
        self.sync_buttons()

    def update_message(self, message: str):
        self.engine_status.setText(message)
        self.update_label = "Güncelleniyor…" if "güncelleniyor" in message.lower() else "Güncellemeler denetleniyor…"
        if self.pending_start:
            self.status.setText(message)
        self.sync_buttons()

    def set_progress(self, value: int, *, reset: bool = False):
        """Animate one overall percentage; only a new job may reset it."""
        value = max(0, min(100, int(value)))
        if self.current:
            value = value if reset else max(self.current.progress, value)
            self.current.progress = value
        self.progress.setRange(0, 100)
        if reset:
            self.progress_animation.stop()
            self.progress_animation.setStartValue(value)
            self.progress_animation.setEndValue(value)
            self.progress.setValue(value)
        elif self.progress_animation.endValue() != value or self.progress.value() > value:
            self.progress_animation.stop()
            self.progress_animation.setStartValue(self.progress.value())
            self.progress_animation.setEndValue(value)
            self.progress_animation.start()

    def update_ready(self, engine: str, version: str, deno: str):
        self.engine, self.deno = Path(engine), Path(deno)
        self.last_check = time.monotonic()
        from datetime import datetime
        checked = datetime.now().strftime("%d.%m.%Y %H:%M")
        self.engine_status.setText(f"İndirme motoru güncel · Nightly {version} · Son denetim: {checked}")
        self.log(f"Nightly {version} doğrulandı. JavaScript motoru hazır.")

    def update_failed(self, error: str):
        self.engine_status.setText("Güncelleme denetlenemedi. İndirme başlamadan önce yeniden denenecek.")
        self.log(f"Güncelleme ayrıntısı: {error}")
        self.last_check = 0

    def update_finished(self):
        thread = self.updater
        self.updater = None
        if thread:
            thread.deleteLater()
        self.sync_buttons()
        if self.pending_start or self.running:
            self.pending_start = False
            if self.last_check:
                self.running = True
                self.next_job()
            else:
                self.offer_cached_engine()

    def offer_cached_engine(self):
        updater = EngineUpdater(self.data_dir / "engine")
        cached = updater.current()
        updater.close()
        deno = find_tool("deno", self.data_dir)
        box = QMessageBox(QMessageBox.Icon.Warning, "Güncelleme denetlenemedi",
                          "En yeni indirme motoru doğrulanamadı. Güncelleme için yeniden deneyin.", parent=self)
        retry = box.addButton("Yeniden dene", QMessageBox.ButtonRole.AcceptRole)
        use_old = None
        if cached and deno:
            use_old = box.addButton("Mevcut sürümle devam et", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Vazgeç", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is retry:
            self.pending_start = True
            self.check_updates()
        elif use_old is not None and box.clickedButton() is use_old:
            self.engine, self.deno = cached, deno
            self.allow_old_for_batch = True
            self.running = True
            self.next_job()
        else:
            self.running = False
            self.sync_buttons()

    def start_batch(self):
        if self.links.toPlainText().strip() and not self.add_links():
            return
        if not any(job.status == "Bekliyor" for job in self.jobs):
            self.links.setFocus()
            self.status.setText("İndirmek için bir YouTube bağlantısı yapıştırın.")
            return
        self.ffmpeg = find_tool("ffmpeg", self.data_dir)
        self.ffprobe = find_tool("ffprobe", self.data_dir)
        if not self.ffmpeg or not self.ffprobe:
            QMessageBox.warning(self, "FFmpeg gerekli",
                                "FFmpeg ve ffprobe bulunamadı. GitHub Releases paketini kullanın veya "
                                f"iki aracı şu klasöre kopyalayın:\n{self.data_dir / 'tools'}")
            return
        self.stop_after, self.allow_old_for_batch = False, False
        self.pending_start = True
        self.check_updates()

    def next_job(self):
        if self.closing:
            return
        if self.stop_after:
            self.finish_batch("İndirme sırası durduruldu.")
            return
        job = next((j for j in self.jobs if j.status == "Bekliyor"), None)
        if not job:
            failed = sum(j.status == "Başarısız" for j in self.jobs)
            self.finish_batch(f"Sıra tamamlandı. Başarısız: {failed}.")
            return
        if not self.allow_old_for_batch and time.monotonic() - self.last_check > 3600:
            self.check_updates()
            return
        self.current, self.cancelled = job, False
        self.slow_stream = None
        self.slow_since = None
        self.speed_retries = 0
        self.speed_restart_pending = False
        self.output_buffer, self.log_lines = "", []
        self.conversion_timer.stop()
        self.clip_progress = {}
        self.download_streams = {}
        self.download_bytes = {}
        self.source_duration = None
        self.stream_count = 1
        self.converting = False
        self.conversion_started = 0.0
        self.conversion_estimate = 0.0
        self.cutting = False
        self.cut_fallback_args = None
        self.clip_destination = None
        job.status, job.error, job.output, job.progress = "Bilgiler alınıyor", "", "", 0
        self.set_progress(0, reset=True)
        self.status.setText("Video bilgileri alınıyor…")
        try:
            Path(job.folder).mkdir(parents=True, exist_ok=True)
            self.clip_workspace = tempfile.TemporaryDirectory(prefix="job-", dir=self.downloads_dir)
            args = download_arguments(job, self.ffmpeg, self.deno,
                                      Path(self.clip_workspace.name) if self.clip_workspace else None)
        except (OSError, ValueError) as error:
            self.finish_job(False, str(error))
            return
        self.persist()
        self.refresh_table()
        self.start_process(str(self.engine), args)
        self.sync_buttons()

    def start_process(self, program: str, args: list[str]):
        if not self.cutting and self.current and self.engine and program == str(self.engine):
            workspace = Path(self.clip_workspace.name) if self.clip_workspace else Path(self.current.folder)
            self.process = FastDownloadProcess(
                self.current, Path(program), args, self.ffmpeg, self.ffprobe, self.deno, workspace, self,
            )
            self.process.readyReadStandardOutput.connect(self.read_output)
            self.process.finished.connect(self.download_finished)
            self.process.errorOccurred.connect(self.process_error)
            self.process.start()
            return
        self.process = QProcess(self)
        environment = QProcessEnvironment()
        for key, value in child_environment().items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        if sys.platform != "win32":
            self.process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.download_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.setProgram(program)
        self.process.setArguments(args)
        self.process.start()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.current:
            self.finish_job(False, self.process.errorString())

    def read_output(self):
        if not self.process:
            return
        self.output_buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.output_buffer.split("\n")
        self.output_buffer = lines.pop()
        for line in lines:
            self.handle_line(line.strip())

    def handle_line(self, line: str):
        job = self.current
        if not job or not line:
            return
        try:
            if self.handle_clip_progress(line):
                return
            if line.startswith("__TITLE__"):
                job.title = json.loads(line[len("__TITLE__"):])
                self.log(f"İndiriliyor: {job.title}")
                if job.start_time is not None:
                    self.status.setText(f"Tam kaynak indiriliyor: {job.title}")
            elif line.startswith("__FILE__"):
                job.output = json.loads(line[len("__FILE__"):])
            elif line.startswith("__STREAMS__"):
                streams = json.loads(line[len("__STREAMS__"):])
                self.stream_count = max(1, len(streams)) if isinstance(streams, list) else 1
            elif line.startswith("__DURATION__"):
                duration = json.loads(line[len("__DURATION__"):])
                if isinstance(duration, (int, float)) and duration > 0:
                    self.source_duration = float(duration)
            elif line.startswith("__PROGRESS__"):
                self.conversion_timer.stop()
                self.converting = False
                info = json.loads(line[len("__PROGRESS__"):])
                self.check_download_speed(info)
                job.status = "İndiriliyor"
                total = info.get("total_bytes") or info.get("total_bytes_estimate")
                downloaded = info.get("downloaded_bytes") or 0
                stream = info.get("filename") or info.get("tmpfilename") or "download"
                fraction = 1.0 if info.get("status") == "finished" else min(1.0, downloaded / total) if total else 0.0
                self.download_streams[stream] = max(self.download_streams.get(stream, 0), fraction)
                self.download_bytes[stream] = max(self.download_bytes.get(stream, 0), int(total or downloaded or 0))
                limit = self.download_progress_limit(job)
                self.set_progress(limit * sum(self.download_streams.values()) / max(self.stream_count, len(self.download_streams)))
                speed = info.get("speed")
                eta = info.get("eta")
                details = f" · {downloaded / 1048576:.1f} MB"
                if total:
                    estimated = "≈ " if not info.get("total_bytes") else ""
                    details += f" / {estimated}{total / 1048576:.1f} MB"
                if speed:
                    details += f" · {speed / 1048576:.1f} MB/sn"
                if eta is not None:
                    details += f" · Kalan: {int(eta) // 60}:{int(eta) % 60:02d}"
                self.status.setText(f"İndiriliyor: {job.title}{details}")
            elif line.startswith("__POST__") or line.startswith(("[ExtractAudio]", "[Merger]", "[VideoRemuxer]")):
                self.start_conversion_progress()
            else:
                self.log_lines.append(line)
                self.log_lines = self.log_lines[-50:]
                self.log(line)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            self.log(line)
        self.refresh_table()

    def check_download_speed(self, info: dict):
        """Refresh a persistently slow connection twice, then let it finish."""
        if self.cancelled or self.speed_restart_pending or self.speed_retries >= 2:
            return
        stream = info.get("filename") or info.get("tmpfilename")
        if stream != self.slow_stream:
            self.slow_stream, self.slow_since = stream, None
        speed = info.get("speed")
        if info.get("status") != "downloading" or speed is None or speed >= 256 * 1024:
            self.slow_since = None
            return
        now = time.monotonic()
        if self.slow_since is None:
            self.slow_since = now
        elif now - self.slow_since >= 20:
            self.speed_retries += 1
            self.speed_restart_pending = True
            self.log(f"Bağlantı 20 saniyedir yavaş; yeniden bağlanılıyor ({self.speed_retries}/2).")
            self.kill_download_process()

    def resume_slow_download(self, job_id: str, process: QProcess):
        if (not self.current or self.current.id != job_id or not self.speed_restart_pending
                or self.process is not process or self.cancelled or self.closing):
            return
        self.speed_restart_pending = False
        self.slow_stream, self.slow_since = None, None
        self.output_buffer = ""
        self.current.output = ""
        self.process.deleteLater()
        self.process = None
        args = download_arguments(self.current, self.ffmpeg, self.deno, Path(self.clip_workspace.name))
        self.start_process(str(self.engine), args)

    def download_progress_limit(self, job: Job) -> int:
        return 75 if job.start_time is not None else 80

    def conversion_progress_range(self, job: Job) -> tuple[int, int]:
        return (75, 84) if job.start_time is not None else (80, 98)

    def estimate_conversion_seconds(self) -> float:
        job = self.current
        if not job:
            return 4.0
        total_bytes = sum(self.download_bytes.values())
        duration = self.source_duration or 0.0
        if job.format == "MP3" and job.start_time is None:
            by_size = total_bytes / (3.5 * 1048576) if total_bytes else 0.0
            by_duration = duration / 35.0 if duration else 0.0
            estimate = max(by_size, by_duration)
            return max(2.0, min(180.0, (estimate + 1.5) if estimate else 6.0))
        by_size = total_bytes / (25.0 * 1048576) if total_bytes else 0.0
        by_duration = duration / 120.0 if duration else 0.0
        estimate = max(by_size, by_duration)
        return max(1.5, min(120.0, (estimate + 1.0) if estimate else 4.0))

    def start_conversion_progress(self):
        job = self.current
        if not job:
            return
        if not self.converting:
            self.converting = True
            self.conversion_started = time.monotonic()
            self.conversion_estimate = self.estimate_conversion_seconds()
            self.conversion_timer.start()
        self.update_conversion_progress()

    def update_conversion_progress(self, elapsed: float | None = None):
        job = self.current
        if not job or not self.converting or self.cutting:
            return
        job.status = "Dönüştürülüyor"
        duration = max(0.5, self.conversion_estimate or self.estimate_conversion_seconds())
        if elapsed is None:
            elapsed = max(0.0, time.monotonic() - self.conversion_started)
        start_progress, end_progress = self.conversion_progress_range(job)
        span = end_progress - start_progress
        self.set_progress(start_progress + min(span, int(elapsed / duration * span)))
        eta = int(max(0, round(duration - elapsed)))
        details = f" · {min(elapsed, duration):.1f} / ≈ {duration:.1f} sn · Kalan: {eta // 60}:{eta % 60:02d}"
        self.status.setText(f"Birleştiriliyor / dönüştürülüyor: {job.title}{details}")
        self.refresh_table()

    def handle_clip_progress(self, line: str) -> bool:
        """Read FFmpeg's newline-delimited progress blocks for section cuts and transcoding."""
        job = self.current
        if not job:
            return False
        is_clip = job.start_time is not None and job.end_time is not None
        if not is_clip and not self.source_duration:
            return False
        key, separator, value = line.partition("=")
        if not separator or key not in {
            "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
            "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress",
        }:
            return False
        self.clip_progress[key] = value.strip()
        if key != "progress":
            return True

        self.conversion_timer.stop()
        self.converting = False
        job.status = "Dönüştürülüyor"
        duration = (job.end_time - job.start_time) if is_clip else (self.source_duration or 1.0)
        # FFmpeg reports output time in microseconds.
        elapsed = self.clip_progress.get("out_time_us", "N/A")
        elapsed = max(0.0, int(elapsed) / 1_000_000) if re.fullmatch(r"-?\d+", elapsed) else None
        size = self.clip_progress.get("total_size", "N/A")
        details = f" · {int(size) / 1048576:.1f} MB kaydedildi" if size.isdigit() else ""
        if elapsed is not None:
            start_pct, span = (85, 13) if is_clip else (80, 18)
            self.set_progress(start_pct + min(span, int(elapsed / duration * span)))
            details += f" · {min(elapsed, duration):.1f} / {duration:.1f} sn"
        speed = self.clip_progress.get("speed", "N/A").removesuffix("x").strip()
        if elapsed is not None and re.fullmatch(r"\d+(?:\.\d+)?", speed) and float(speed) > 0:
            eta = int(max(0, duration - elapsed) / float(speed))
            details += f" · Kalan: {eta // 60}:{eta % 60:02d}"
        prefix = "Yerel dosya kesiliyor" if is_clip else "Birleştiriliyor / dönüştürülüyor"
        self.status.setText(f"{prefix}: {job.title}{details}")
        self.clip_progress.clear()
        self.refresh_table()
        return True

    def download_finished(self, code: int, exit_status):
        self.read_output()
        if self.output_buffer:
            self.handle_line(self.output_buffer.strip())
            self.output_buffer = ""
        self.conversion_timer.stop()
        self.converting = False
        if not self.current:
            return
        if self.cancelled:
            self.finish_job(False, "Kullanıcı tarafından iptal edildi.")
        elif self.speed_restart_pending:
            # Keep the workspace and .part files: yt-dlp re-extracts fresh URLs
            # and resumes instead of downloading the same bytes again.
            self.current.status = "Bilgiler alınıyor"
            self.status.setText("Yavaş bağlantı yenileniyor; indirme kaldığı yerden sürdürülecek…")
            self.refresh_table()
            job_id = self.current.id
            process = self.process
            QTimer.singleShot(self.speed_retries * 5000, lambda: self.resume_slow_download(job_id, process))
        elif self.cutting and code != 0 and self.cut_fallback_args:
            fallback_args = self.cut_fallback_args
            self.cut_fallback_args = None
            self.log("Donanım kodlayıcı kesimde başarısız oldu; uyumlu CPU kodlayıcıya geçiliyor…")
            self.clip_progress = {}
            self.output_buffer = ""
            if self.process:
                self.process.deleteLater()
                self.process = None
            self.start_process(str(self.ffmpeg), fallback_args)
        elif code != 0:
            self.finish_job(False, "\n".join(self.log_lines) or f"İşlem hata kodu: {code}")
        elif not self.current.output or not Path(self.current.output).is_file():
            self.finish_job(False, "Çıktı dosyası bulunamadı.")
        elif self.current.start_time is not None and not self.cutting:
            self.start_local_cut()
        else:
            self.verify_output()

    def start_local_cut(self):
        self.conversion_timer.stop()
        self.converting = False
        job = self.current
        source = Path(job.output)
        self.clip_destination = Path(job.folder) / source.with_suffix("." + job.format.lower()).name
        if self.clip_destination.exists():
            job.output = str(self.clip_destination)
            self.clip_destination = None
            self.verify_output()
            return
        if not self.clip_workspace:
            self.finish_job(False, "Kesit için geçici klasör bulunamadı.")
            return
        output = Path(self.clip_workspace.name) / ("clip." + job.format.lower())
        self.cutting = True
        self.cut_fallback_args = None
        self.clip_progress = {}
        self.output_buffer = ""
        self.process.deleteLater()
        self.process = None
        job.output, job.status = str(output), "Dönüştürülüyor"
        self.status.setText(f"İndirme tamamlandı. Yerel dosya kesiliyor: {job.title}")
        self.set_progress(85)
        self.refresh_table()
        video_codec_args: list[str] | None = None
        copy_audio = True
        if job.format == "MP4":
            vcodec, acodec = probe_media_codecs(self.ffprobe, source)
            if vcodec and (vcodec != "h264" or (acodec and acodec != "aac")):
                copy_audio = (acodec == "aac")
                encoders = available_h264_encoders(self.ffmpeg)
                video_codec_args = encoders[0][1]
                if len(encoders) > 1:
                    self.cut_fallback_args = cut_arguments(
                        job, source, output, encoders[-1][1], copy_audio=copy_audio
                    )
        self.start_process(str(self.ffmpeg), cut_arguments(
            job, source, output, video_codec_args, copy_audio=copy_audio
        ))

    def verify_output(self):
        self.conversion_timer.stop()
        self.converting = False
        if not self.clip_destination and self.clip_workspace:
            source = Path(self.current.output)
            if source.parent == Path(self.clip_workspace.name):
                destination = Path(self.current.folder) / source.name
                if destination.exists():
                    self.current.output = str(destination)
                else:
                    self.clip_destination = destination
        self.current.status = "Doğrulanıyor"
        self.set_progress(99)
        self.refresh_table()
        self.probe = QProcess(self)
        environment = QProcessEnvironment()
        for key, value in child_environment().items():
            environment.insert(key, value)
        self.probe.setProcessEnvironment(environment)
        self.probe.finished.connect(self.probe_finished)
        self.probe.errorOccurred.connect(self.probe_error)
        self.probe.start(str(self.ffprobe), ["-v", "error", "-show_streams", "-show_format", "-of", "json", self.current.output])
        QTimer.singleShot(30000, self.probe, self.probe.kill)

    def probe_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.current:
            self.finish_job(False, "Çıktı dosyası doğrulanamadı: ffprobe başlatılamadı.")

    def probe_finished(self, code: int, exit_status):
        if not self.current:
            return
        if self.cancelled:
            self.finish_job(False, "Kullanıcı tarafından iptal edildi.")
            return
        try:
            metadata = json.loads(bytes(self.probe.readAllStandardOutput()))
            streams = metadata.get("streams", [])
            audio = any(s.get("codec_type") == "audio" for s in streams)
            video_streams = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
            video = bool(video_streams) and all(s.get("codec_name", "h264") == "h264" for s in video_streams)
            valid = (code == 0 and audio and float(metadata["format"].get("duration", 0)) > 0
                     and Path(self.current.output).suffix.lower() == "." + self.current.format.lower())
            if self.current.format == "MP4":
                valid = valid and video
            if valid and self.clip_destination:
                if self.clip_destination.exists():
                    raise FileExistsError("Hedef dosya zaten var.")
                # Settings and the destination may be on different drives.
                shutil.move(self.current.output, self.clip_destination)
                self.current.output = str(self.clip_destination)
            self.finish_job(bool(valid), "Çıktı dosyası beklenen ses/video biçiminde değil." if not valid else "")
        except (ValueError, KeyError, TypeError, OSError):
            self.finish_job(False, "Çıktı dosyası doğrulanamadı.")

    def finish_job(self, success: bool, error: str = ""):
        self.conversion_timer.stop()
        self.converting = False
        job = self.current
        if not job:
            return
        if self.closing:
            job.status, job.progress, job.error, job.output = "Bekliyor", 0, "", ""
        else:
            job.status = "Tamamlandı" if success else "İptal edildi" if self.cancelled else "Başarısız"
            job.error = error
            if success:
                job.progress = 100
        self.log(f"{job.status}: {job.title}")
        if error:
            self.log(error)
        self.current = None
        if self.process:
            self.process.deleteLater()
            self.process = None
        if self.probe:
            self.probe.deleteLater()
            self.probe = None
        if self.clip_workspace:
            try:
                self.clip_workspace.cleanup()
            except OSError as cleanup_error:
                self.log(f"Geçici indirme dosyaları temizlenemedi: {cleanup_error}")
            self.clip_workspace = None
        self.clip_destination = None
        self.cutting = False
        self.cut_fallback_args = None
        self.persist()
        self.refresh_table()
        self.set_progress(job.progress)
        self.sync_buttons()
        QTimer.singleShot(300, self.next_job)

    def finish_batch(self, text: str):
        self.running = False
        self.status.setText(text)
        self.progress.setRange(0, 100)
        self.sync_buttons()

    def request_stop(self):
        self.stop_after = True
        self.status.setText("Geçerli işlem tamamlanınca sıra durdurulacak.")

    def cancel_current(self):
        if not self.current:
            return
        self.cancelled = True
        self.conversion_timer.stop()
        self.converting = False
        if self.probe:
            self.probe.kill()
        if self.speed_restart_pending and (not self.process or self.process.state() == QProcess.ProcessState.NotRunning):
            self.finish_job(False, "Kullanıcı tarafından iptal edildi.")
        else:
            self.kill_download_process()
        self.sync_buttons()

    def kill_download_process(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            pid = self.process.processId()
            if pid and pid > 0:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    import signal
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.process.kill()

    def closeEvent(self, event):
        if self.updater is not None:
            QMessageBox.information(self, "Güncelleme sürüyor", "Güncelleme denetimi bitince pencereyi kapatabilirsiniz.")
            event.ignore()
            return
        self.closing = True
        self.stop_after = True
        self.running = False
        interrupted = self.current
        if interrupted:
            self.cancel_current()
        # A Python HTTP worker may still be inside a socket read. Keep Qt and
        # its thread owners alive until all workers have exited; never destroy
        # the window after an arbitrary timeout while its QThread is running.
        if any(proc and proc.state() != QProcess.ProcessState.NotRunning
               for proc in (self.process, self.probe)):
            self.status.setText("İndirme durduruluyor; uygulama kapatılıyor…")
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        if interrupted:
            interrupted.status, interrupted.progress = "Bekliyor", 0
            interrupted.error, interrupted.output = "", ""
        self.current = None
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("folder", self.folder.text())
        self.settings.setValue("format", self.format.currentText())
        self.settings.setValue("quality", self.quality.currentData())
        self.persist()
        event.accept()


import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox



def main() -> int:
    smoke_test = "--smoke-test" in sys.argv

    def smoke_stage(message):
        if smoke_test:
            with (application_data_dir() / "smoke.log").open("a", encoding="utf-8") as log:
                log.write(message + "\n")

    if smoke_test:
        import faulthandler
        application_data_dir().mkdir(parents=True, exist_ok=True)
        # Keep the file alive through Qt teardown; native failures may bypass
        # Python exceptions and the captured Windows console streams.
        global _smoke_fault_log
        diagnostic_path = application_data_dir() / "smoke.log"
        diagnostic_path.write_text("", encoding="utf-8")
        _smoke_fault_log = diagnostic_path.open("a", encoding="utf-8")
        faulthandler.enable(file=_smoke_fault_log)
        smoke_stage("Starting Qt application")
    if "--build-icon" in sys.argv:
        index = sys.argv.index("--build-icon")
        target_dir = Path(sys.argv[index + 1]) if index + 1 < len(sys.argv) else Path("build/icons")
        path = build_icon_file(target_dir)
        print(path)
        return 0
    if "--check-bundle" in sys.argv:
        root = application_data_dir()
        root.mkdir(parents=True, exist_ok=True)
        result = {"frozen": bool(getattr(sys, "frozen", False))}
        for name in ("ffmpeg", "ffprobe"):
            path = find_tool(name, root)
            if path is None:
                raise RuntimeError(f"Bundled tool missing: {name}")
            if getattr(sys, "frozen", False) and not path.is_relative_to(Path(sys._MEIPASS)):
                raise RuntimeError(f"Bundle check used external tool: {path}")
            result[name] = run_tool([str(path), "-version"]).splitlines()[0]
        (root / "bundle-check.json").write_text(json.dumps(result), encoding="utf-8")
        return 0
    if "--check-engine" in sys.argv:
        index = sys.argv.index("--check-engine")
        root = Path(sys.argv[index + 1])
        updater = EngineUpdater(root / "engine", print)
        try:
            engine, version = updater.update()
            deno = updater.ensure_deno(root)
            result = {"engine": str(engine), "version": version,
                      "deno": run_tool([str(deno), "--version"]),
                      "frozen": bool(getattr(sys, "frozen", False))}
            (root / "engine-check.json").write_text(json.dumps(result))
            print(json.dumps(result))
            return 0
        finally:
            updater.close()
    app = QApplication(sys.argv)
    smoke_stage(f"Qt application ready; platform={app.platformName()}")
    app.setOrganizationName("YTIndir")
    app.setApplicationName("YTIndir")
    app.setWindowIcon(app_icon())
    # Overrides are useful for portable installations and isolated smoke tests.
    data_dir = application_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "application.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "YT İndir zaten açık", "Açık olan YT İndir penceresini kullanın.")
        return 1
    smoke_stage("Creating window")
    window = Window(data_dir, auto_update=not smoke_test)
    smoke_stage("Showing window")
    window.show()
    if smoke_test:
        def capture():
            smoke_stage("Capturing window")
            image_path = data_dir / "preview.png"
            if not window.grab().save(str(image_path)):
                smoke_stage("Screenshot failed")
                app.exit(1)
                return
            (data_dir / "smoke.json").write_text(json.dumps({"frozen": bool(getattr(sys, "frozen", False)),
                                                           "window": window.windowTitle(), "screenshot": str(image_path),
                                                           "platform": app.platformName()}))
            smoke_stage("Closing window")
            window.close()
        # Give the callback an explicit Qt owner and release it before deleting
        # the window. A singleShot helper can retain the captured GUI objects
        # until Python shutdown, after QApplication has started tearing down.
        capture_timer = QTimer(window)
        capture_timer.setSingleShot(True)
        capture_timer.timeout.connect(capture)
        capture_timer.start(500)
    smoke_stage("Entering event loop")
    try:
        result = app.exec()
        smoke_stage(f"Event loop finished; exit={result}")
        return result
    finally:
        if smoke_test:
            capture_timer.stop()
            capture_timer.timeout.disconnect(capture)
        # Destroy the widget tree while QApplication is still alive, rather
        # than leaving Qt/Python ownership cycles to interpreter shutdown.
        smoke_stage("Deleting window")
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        lock.unlock()
        smoke_stage("Window deleted; application lock released")


if __name__ == "__main__":
    raise SystemExit(main())

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
    jobs = [Job(**j) for j in json.loads(path.read_text(encoding="utf-8"))]
    jobs = [job for job in jobs if job.status == "Bekliyor" or job.status in ACTIVE_STATUSES]
    for job in jobs:
        if job.status in ACTIVE_STATUSES:
            job.status, job.progress, job.error, job.output = "Bekliyor", 0, "", ""
    return jobs


def download_arguments(job: Job, ffmpeg: Path, deno: Path) -> list[str]:
    args = ["--ignore-config", "--no-playlist", "--newline", "--no-colors", "--progress",
            "--encoding", "utf-8", "--socket-timeout", "20", "--retries", "3",
            "--fragment-retries", "3", "--extractor-retries", "3", "--continue",
            "--no-overwrites", "--windows-filenames", "--ffmpeg-location", str(ffmpeg.parent),
            "--no-js-runtimes", "--js-runtimes", f"deno:{deno}",
            "--progress-template", "download:__PROGRESS__%(progress)j",
            "--progress-template", "postprocess:__POST__%(progress)j",
            "--print", "before_dl:__TITLE__%(title)j", "--print", "after_move:__FILE__%(filepath)j",
            "--no-simulate", "-P", job.folder]
    # Quality is part of the name so changing it cannot silently reuse another output.
    section = ""
    if job.start_time is not None or job.end_time is not None:
        if (job.start_time is None or job.end_time is None
                or not 0 <= job.start_time < job.end_time < float("inf")):
            raise ValueError("Geçersiz kesit aralığı.")
        section = f" [{timestamp_seconds(job.start_time)}-{timestamp_seconds(job.end_time)}s]"
        args += ["--download-sections", f"*{timestamp_seconds(job.start_time)}-{timestamp_seconds(job.end_time)}",
                 "--downloader", "ffmpeg"]
        if job.format == "MP4":
            args += ["--force-keyframes-at-cuts"]
    template = f"%(title).140B [%(id)s] [{job.format}-{job.quality}]{section}.%(ext)s"
    args += ["-o", template]
    if job.format == "MP3":
        args += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                 "--audio-quality", f"{job.quality}K", "--embed-metadata"]
    else:
        limit = "" if job.quality == "best" else f"[height<={int(job.quality)}]"
        args += ["-f", f"bv*{limit}+ba/b{limit}", "--merge-output-format", "mp4",
                 "--remux-video", "mp4"]
    return args + ["--", job.url]


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


import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QMenu, QScrollArea, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)



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


class Window(QMainWindow):
    def __init__(self, data_dir: Path, *, auto_update: bool = True):
        super().__init__()
        self.data_dir = data_dir
        self.queue_file = data_dir / "queue.json"
        self.settings = StateManager(data_dir)
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
        self.log_lines: list[str] = []
        self.setWindowTitle("YT İndir · MP3 ve MP4")
        self.resize(760, 620)
        self.setMinimumSize(640, 540)
        self.build_ui()
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
        clip_help = QLabel("Örnek: 01:00 → 01:30, yalnızca bu 30 saniyeyi indirir.", objectName="subheading")
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
        self.start.setText("Hazırlanıyor…" if updating else "İndiriliyor…" if self.running else "İndir")
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
            state = f"{job.status} · %{job.progress}" if job.status == "İndiriliyor" else job.status
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
        self.updater.message.connect(self.engine_status.setText)
        self.updater.ready.connect(self.update_ready)
        self.updater.failed.connect(self.update_failed)
        self.updater.finished.connect(self.update_finished)
        self.updater.start()
        self.sync_buttons()

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
        self.output_buffer, self.log_lines = "", []
        job.status, job.error, job.output, job.progress = "Bilgiler alınıyor", "", "", 0
        self.progress.setRange(0, 0)
        self.status.setText("Video bilgileri alınıyor…")
        try:
            Path(job.folder).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.finish_job(False, str(error))
            return
        self.persist()
        self.refresh_table()
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
        self.process.setProgram(str(self.engine))
        self.process.setArguments(download_arguments(job, self.ffmpeg, self.deno))
        self.process.start()
        self.sync_buttons()

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
            if line.startswith("__TITLE__"):
                job.title = json.loads(line[len("__TITLE__"):])
                self.log(f"İndiriliyor: {job.title}")
            elif line.startswith("__FILE__"):
                job.output = json.loads(line[len("__FILE__"):])
            elif line.startswith("__PROGRESS__"):
                info = json.loads(line[len("__PROGRESS__"):])
                job.status = "İndiriliyor"
                total = info.get("total_bytes") or info.get("total_bytes_estimate")
                if total:
                    job.progress = min(99, int((info.get("downloaded_bytes") or 0) / total * 100))
                    self.progress.setRange(0, 100)
                    self.progress.setValue(job.progress)
                speed = info.get("speed")
                eta = info.get("eta")
                details = f" · {speed / 1048576:.1f} MB/sn" if speed else ""
                if eta is not None:
                    details += f" · Kalan: {int(eta) // 60}:{int(eta) % 60:02d}"
                self.status.setText(f"İndiriliyor: {job.title}{details}")
            elif line.startswith("__POST__") or line.startswith(("[ExtractAudio]", "[Merger]", "[VideoRemuxer]")):
                job.status = "Dönüştürülüyor"
                self.progress.setRange(0, 0)
                self.status.setText(f"Birleştiriliyor / dönüştürülüyor: {job.title}")
            else:
                self.log_lines.append(line)
                self.log_lines = self.log_lines[-50:]
                self.log(line)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            self.log(line)
        self.refresh_table()

    def download_finished(self, code: int, exit_status):
        self.read_output()
        if self.output_buffer:
            self.handle_line(self.output_buffer.strip())
            self.output_buffer = ""
        if not self.current:
            return
        if self.cancelled:
            self.finish_job(False, "Kullanıcı tarafından iptal edildi.")
        elif code != 0:
            self.finish_job(False, "\n".join(self.log_lines) or f"İşlem hata kodu: {code}")
        elif not self.current.output or not Path(self.current.output).is_file():
            self.finish_job(False, "Çıktı dosyası bulunamadı.")
        else:
            self.verify_output()

    def verify_output(self):
        self.current.status = "Doğrulanıyor"
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
            video = any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams)
            valid = (code == 0 and audio and float(metadata["format"].get("duration", 0)) > 0
                     and Path(self.current.output).suffix.lower() == "." + self.current.format.lower())
            if self.current.format == "MP4":
                valid = valid and video
            self.finish_job(bool(valid), "Çıktı dosyası beklenen ses/video biçiminde değil." if not valid else "")
        except (ValueError, KeyError, TypeError):
            self.finish_job(False, "Çıktı dosyası doğrulanamadı.")

    def finish_job(self, success: bool, error: str = ""):
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
        self.persist()
        self.refresh_table()
        self.progress.setRange(0, 100)
        self.progress.setValue(job.progress)
        self.sync_buttons()
        QTimer.singleShot(0, self.next_job)

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
        if self.probe:
            self.probe.kill()
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            pid = self.process.processId()
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
        self.sync_buttons()

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
            if self.process:
                self.process.waitForFinished(3000)
            if self.probe:
                self.probe.waitForFinished(3000)
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

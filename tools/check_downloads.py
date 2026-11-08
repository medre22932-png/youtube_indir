"""Test the actual Qt queue + yt-dlp + FFmpeg pipeline against a local media server.

Usage: python tools/check_downloads.py ENGINE_DATA_ROOT OUTPUT_DIRECTORY
The engine root is populated by tools/check_engine.py first.
"""
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from main import Job, find_tool, run_tool
from main import EngineUpdater
from main import Window


def main():
    engine_root, root = Path(sys.argv[1]), Path(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    media = root / "fixture.mp4"
    ffmpeg = find_tool("ffmpeg", root)
    ffprobe = find_tool("ffprobe", root)
    run_tool([str(ffmpeg), "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24",
              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(media)])
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = QApplication([])
    app.setOrganizationName("YTIndirTest")
    app.setApplicationName("YTIndirTest")
    updater = EngineUpdater(engine_root / "engine")
    engine = updater.current()
    updater.close()
    if not engine:
        raise RuntimeError("Run check_engine.py first")
    window = Window(root, auto_update=False)
    url = f"http://127.0.0.1:{server.server_port}/fixture.mp4"
    window.jobs = [Job(url, "MP3", "128", str(root / "outputs")),
                   Job(url, "MP4", "best", str(root / "outputs"))]
    window.engine, window.deno = engine, find_tool("deno", engine_root)
    window.ffmpeg, window.ffprobe = ffmpeg, ffprobe
    window.last_check, window.running = time.monotonic(), True
    window.show()
    timer = QTimer()
    failures = []
    def poll():
        if window.jobs[1].status not in ("Bekliyor",) and window.jobs[0].status != "Tamamlandı":
            failures.append("Queue advanced before previous conversion/validation completed")
        if not window.running:
            timer.stop()
            window.grab().save(str(root / "pipeline.png"))
            for job in window.jobs:
                if job.status != "Tamamlandı":
                    failures.append(f"{job.format}: {job.error}")
            if not failures:
                info = json.loads(run_tool([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", window.jobs[0].output]))
                if int(info["streams"][0]["bit_rate"]) != 128000:
                    failures.append("MP3 output bitrate is not 128 kbps")
            (root / "result.json").write_text(json.dumps({"passed": not failures, "failures": failures,
                "jobs": [{"format": j.format, "status": j.status, "output": j.output} for j in window.jobs]}, indent=2))
            app.exit(1 if failures else 0)
    timer.timeout.connect(poll)
    timer.start(25)
    QTimer.singleShot(120000, lambda: app.exit(2))
    QTimer.singleShot(0, window.next_job)
    code = app.exec()
    if window.current:
        window.stop_after = True
        window.cancel_current()
        if window.process:
            window.process.waitForFinished(3000)
    server.shutdown()
    print((root / "result.json").read_text() if (root / "result.json").exists() else window.details.toPlainText())
    return code


if __name__ == "__main__":
    raise SystemExit(main())

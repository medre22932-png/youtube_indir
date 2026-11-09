"""Offline regression checks for range integrity, reconnects and Qt shutdown."""
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

import youtube_indir as app


class Response:
    def __init__(self, data, start, end, *, status=206):
        self.status_code = status
        self.headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}",
                        "Content-Length": str(end - start + 1)}
        self.body = data[start:end + 1]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


class Server:
    def __init__(self, data):
        self.data = data
        self.ranges = []
        self.sessions = []
        self.fail_at = None
        self.status = 206
        self.lock = threading.Lock()

    def session(self):
        server = self

        class Session:
            closed = False

            def mount(self, *args):
                pass

            def get(self, url, *, headers, **kwargs):
                start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"]).groups())
                assert headers["Accept-Encoding"] == "identity"
                with server.lock:
                    server.ranges.append((start, end))
                if start == server.fail_at:
                    raise InterruptedError("Simulated interruption")
                return Response(server.data, start, end, status=server.status)

            def close(self):
                self.closed = True

        session = Session()
        with self.lock:
            self.sessions.append(session)
        return session


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = app.Job("https://youtu.be/example", "MP4", "1080", str(self.root))
        self.proc = app.FastDownloadProcess(self.job, Path(app.__file__), [], None, None, None, self.root)
        self.dest = self.root / "raw_video.webm"
        self.server = Server(bytes(range(31)))
        self.patches = [patch.object(app, "RANGE_CHUNK_SIZE", 8),
                        patch.object(app.requests, "Session", self.server.session)]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)

    def download(self, *, stream_id="137", url="https://cdn.example/video?token=new", pipe=None):
        return self.proc._parallel_download_file(
            url, {}, len(self.server.data), self.dest, 0, len(self.server.data),
            pipe_stdin=pipe, stream_id=stream_id)

    def test_ordered_download_and_sessions_closed(self):
        self.download()
        self.assertEqual(self.dest.read_bytes(), self.server.data)
        self.assertEqual(sorted(self.server.ranges), [(0, 7), (8, 15), (16, 23), (24, 30)])
        self.assertTrue(all(session.closed for session in self.server.sessions))

    def test_interrupted_download_resumes_prefix_and_replays_pipe(self):
        self.server.fail_at = 8
        with self.assertRaises(InterruptedError):
            self.download(url="https://cdn.example/video?token=old")
        self.assertEqual(self.dest.read_bytes(), self.server.data[:8])
        self.assertTrue(all(session.closed for session in self.server.sessions))
        self.server.fail_at = None
        self.server.ranges.clear()
        pipe = io.BytesIO()
        self.download(pipe=pipe)
        self.assertEqual(self.dest.read_bytes(), self.server.data)
        self.assertEqual(pipe.getvalue(), self.server.data)
        self.assertNotIn((0, 7), self.server.ranges)

    def test_completed_source_is_replayed_without_network(self):
        self.download()
        self.server.ranges.clear()
        pipe = io.BytesIO()
        self.download(pipe=pipe)
        self.assertEqual(self.server.ranges, [])
        self.assertEqual(pipe.getvalue(), self.server.data)

    def test_changed_format_or_size_discards_old_prefix(self):
        self.download()
        self.server.ranges.clear()
        self.server.data = b"x" * 31
        self.download(stream_id="248")
        self.assertIn((0, 7), self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), self.server.data)
        self.server.ranges.clear()
        self.server.data = b"y" * 32
        self.download(stream_id="248")
        self.assertIn((0, 7), self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), self.server.data)

    def test_partial_disk_chunk_is_redownloaded(self):
        self.download()
        self.dest.write_bytes(self.server.data[:11])
        self.server.ranges.clear()
        self.download()
        self.assertNotIn((0, 7), self.server.ranges)
        self.assertIn((8, 15), self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), self.server.data)

    def test_ignored_range_is_not_written(self):
        self.server.status = 200
        with patch.object(self.proc._cancel_event, "wait", return_value=False):
            with self.assertRaises(ValueError):
                self.download()
        self.assertEqual(self.dest.stat().st_size, 0)
        self.assertTrue(all(session.closed for session in self.server.sessions))

    def test_bad_response_headers_are_rejected(self):
        for change in ({"Content-Range": "bytes 8-15/31"},
                       {"Content-Range": "bytes 0-7/32"},
                       {"Content-Range": "bytes 0-7/*"},
                       {"Content-Length": "9"}, {"Content-Encoding": "gzip"}):
            with self.subTest(change=change):
                response = Response(self.server.data, 0, 7)
                response.headers.update(change)
                with self.assertRaises(ValueError):
                    app.validate_range_response(response, 0, 7, 31)

    def test_short_and_oversized_bodies_are_not_written(self):
        for transform in (lambda data: data[:-1], lambda data: data + b"extra"):
            with self.subTest(transform=transform):
                with patch.object(Response, "iter_content", lambda response, size: iter([transform(response.body)])), \
                     patch.object(self.proc._cancel_event, "wait", return_value=False):
                    with self.assertRaises((RuntimeError, ValueError)):
                        self.download()
                self.assertEqual(self.dest.stat().st_size, 0)

    def test_probe_requires_exact_single_byte_range(self):
        session = self.server.session()
        stream = {"url": "https://cdn.example/video", "protocol": "https"}
        self.assertEqual(self.proc._probe_stream_range(session, stream), 31)
        self.server.status = 200
        self.assertIsNone(self.proc._probe_stream_range(session, stream))

    def test_pipeline_names_full_downloads_and_sections_from_title(self):
        # Extraction JSON need not include yt-dlp's private _filename field.
        for format_name in ("MP4", "MP3"):
            for section in (False, True):
                with self.subTest(format=format_name, section=section):
                    self.job.format = format_name
                    self.job.quality = "1080" if format_name == "MP4" else "192"
                    self.job.start_time = 10 if section else None
                    self.job.end_time = 20 if section else None
                    self.proc.ffmpeg = Path(app.__file__)
                    info = {"title": "Örnek video: Başlık / test?", "id": "video123", "ext": "m4a" if format_name == "MP3" else "mp4",
                            "url": "https://cdn.example/video", "format_id": "137",
                            "vcodec": "avc1" if format_name == "MP4" else "none", "acodec": "mp4a"}

                    def spawn(cmd, **kwargs):
                        class Encoder:
                            stdout = io.BytesIO()
                            stdin = io.BytesIO()

                            def wait(self):
                                Path(cmd[-1]).write_bytes(b"encoded media")
                                return 0
                        return Encoder()

                    def mux(cmd, **kwargs):
                        Path(cmd[-1]).write_bytes(b"merged media")
                        return 0

                    with patch.object(self.proc, "_extract_info", return_value=info), \
                         patch.object(self.proc, "_spawn_child", side_effect=spawn), \
                         patch.object(self.proc, "_run_ffmpeg_with_progress", side_effect=mux), \
                         patch.object(self.proc, "_probe_stream_range", return_value=31):
                        self.assertTrue(self.proc._try_parallel_pipeline())
                    source = Path(self.proc._captured_file)
                    self.assertTrue(source.is_file())
                    final_name = source.with_suffix("." + format_name.lower()).name
                    self.assertTrue(final_name.startswith("Örnek video_ Başlık _ test_ [video123]"))
                    if section:
                        self.assertIn("[10-20s]", final_name)
                    self.assertNotIn("output.", final_name)
                    self.assertNotIn("raw_audio", final_name)

    def test_skip_transcoding_muxes_high_resolution_source_video(self):
        self.job.skip_transcoding = True
        self.proc.ffmpeg = Path(app.__file__)
        for quality in ("1440", "2160"):
            for codec in ("av01", "vp9"):
                for section in (False, True):
                    with self.subTest(quality=quality, codec=codec, section=section):
                        self.job.quality = quality
                        self.job.start_time = 10 if section else None
                        self.job.end_time = 20 if section else None
                        info = {"title": "Original", "id": "video123", "ext": "webm",
                                "url": "https://cdn.example/video", "format_id": "401",
                                "vcodec": codec, "acodec": "opus"}
                        def mux(cmd, **kwargs):
                            self.assertIn("-c:v", cmd)
                            self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
                            Path(cmd[-1]).write_bytes(b"original video")
                            return 0
                        with patch.object(self.proc, "_extract_info", return_value=info), \
                             patch.object(self.proc, "_run_ffmpeg_with_progress", side_effect=mux) as runner, \
                             patch.object(self.proc, "_probe_stream_range", return_value=31), \
                             patch.object(self.proc, "_transcode_local_mp4") as transcode:
                            self.assertTrue(self.proc._try_parallel_pipeline())
                            runner.assert_called_once()
                            transcode.assert_not_called()
                        self.assertIn("-original]", self.proc._captured_file)
                        self.assertEqual(Path(self.proc._captured_file).suffix, ".mp4")

    def test_skip_transcoding_scope_and_queue_persistence(self):
        self.job.skip_transcoding = True
        for format_name, quality, expected in (("MP4", "1440", True), ("MP4", "2160", True),
                                               ("MP4", "1080", False), ("MP3", "192", False)):
            self.job.format, self.job.quality = format_name, quality
            self.assertEqual(self.job.keep_source_video, expected)
        queue = self.root / "queue.json"
        app.save_jobs(queue, [self.job])
        self.assertTrue(app.load_jobs(queue)[0].skip_transcoding)
        entries = json.loads(queue.read_text())
        entries[0].pop("skip_transcoding")
        queue.write_text(json.dumps(entries))
        self.assertFalse(app.load_jobs(queue)[0].skip_transcoding)


class PipelineProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = app.Window(Path(self.directory.name), auto_update=False)
        self.window.current = app.Job("https://youtu.be/example", "MP4", "1440", self.directory.name)
        self.window.handle_line('__DURATION__100')
        self.window.handle_line('__TRANSCODE_MODE__')

    def tearDown(self):
        self.window.current = None
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def download(self, fraction, eta, status="downloading"):
        self.window.handle_line('__PROGRESS__' + json.dumps({
            "filename": "video", "total_bytes": 1000, "downloaded_bytes": fraction * 1000,
            "eta": eta, "status": status,
        }))

    def test_advanced_setting_defaults_off_and_persists(self):
        self.assertFalse(self.window.skip_transcoding.isChecked())
        self.assertEqual(self.window.clip_enabled.text(), "Yalnızca belli bir bölümü kaydet")
        self.window.skip_transcoding.setChecked(True)
        restored = app.Window(Path(self.directory.name), auto_update=False)
        try:
            self.assertTrue(restored.skip_transcoding.isChecked())
            restored.skip_transcoding.setChecked(False)
            self.assertFalse(app.StateManager(Path(self.directory.name)).value("skip_transcoding"))
        finally:
            restored.close()
            restored.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def convert(self, seconds, speed="2x"):
        for line in (f"out_time_us={seconds * 1000000}", f"speed={speed}", "progress=continue"):
            self.window.handle_line(line)

    def test_conversion_controls_progress_and_eta_on_both_updates(self):
        self.download(.8, 5)
        self.assertEqual(self.window.current.progress, 0)
        self.assertIn("hesaplanıyor", self.window.status.text())
        self.convert(20)
        self.assertEqual(self.window.current.progress, 19)
        self.assertIn("Kalan: 0:40", self.window.status.text())
        self.download(.9, 2)
        self.assertEqual(self.window.current.progress, 19)
        self.assertIn("Kalan: 0:40", self.window.status.text())
        self.convert(40)
        self.assertEqual(self.window.current.progress, 39)
        self.assertIn("Kalan: 0:30", self.window.status.text())

    def test_download_completion_does_not_jump_ahead_of_conversion(self):
        self.download(.8, 5)
        self.convert(20)
        self.download(1, 0, "finished")
        self.assertEqual(self.window.current.progress, 19)
        self.convert(40)
        self.assertEqual(self.window.current.progress, 39)
        self.assertIn("Kalan: 0:30", self.window.status.text())
        self.convert(100)
        self.assertEqual(self.window.current.progress, 98)

    def test_download_controls_progress_when_it_is_behind(self):
        self.download(.2, 120)
        self.convert(80)
        self.assertEqual(self.window.current.progress, 19)
        self.assertIn("Kalan: 2:00", self.window.status.text())
        self.assertEqual(self.window.current.status, "İndiriliyor")


class ShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def test_close_waits_for_worker_without_blocking_qt(self):
        with tempfile.TemporaryDirectory() as directory:
            window = app.Window(Path(directory), auto_update=False)
            job = app.Job("https://youtu.be/example", "MP4", "1080", directory)
            window.jobs = [job]
            window.current = job
            window.speed_restart_pending = False
            release = threading.Event()
            entered = threading.Event()
            finished = threading.Event()
            proc = app.FastDownloadProcess(job, Path(app.__file__), [], None, None, None,
                                           Path(directory), window)

            def pending_read():
                entered.set()
                release.wait(5)
                return False

            proc._try_parallel_pipeline = pending_read
            proc._thread.finished.connect(finished.set)
            proc.finished.connect(window.download_finished)
            window.process = proc
            window.show()
            proc.start()
            self.assertTrue(entered.wait(2))
            try:
                window.close()
                self.assertTrue(window.isVisible())
                self.assertTrue(proc._thread.isRunning())
                QTest.qWait(120)  # Qt can process repaint/close callbacks while waiting.
                self.assertTrue(window.isVisible())
                release.set()
                for _ in range(100):
                    QTest.qWait(10)
                    if not window.isVisible():
                        break
                self.assertFalse(window.isVisible())
                self.assertTrue(finished.is_set())
                self.assertEqual(job.status, "Bekliyor")
            finally:
                release.set()
                if isValid(proc):
                    proc.waitForFinished(2000)
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()

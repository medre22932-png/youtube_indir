import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from youtube_indir import Job, Window, download_arguments, cut_arguments, cleanup_downloads


class DownloadProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = Window(Path(self.directory.name), auto_update=False)
        self.job = Job("https://youtu.be/example", "MP4", "720", self.directory.name,
                       title="Example", start_time=60, end_time=90)
        self.window.current = self.job
        self.window.jobs = [self.job]

    def tearDown(self):
        self.window.current = None
        self.window.close()
        self.directory.cleanup()

    def test_sections_download_the_full_source_natively(self):
        args = download_arguments(self.job, Path("/tools/ffmpeg"), Path("/tools/deno"))
        self.assertNotIn("--download-sections", args)
        self.assertNotIn("--downloader", args)
        self.assertNotIn("--downloader-args", args)
        self.job.start_time = self.job.end_time = None
        self.assertNotIn("--downloader-args", download_arguments(
            self.job, Path("/tools/ffmpeg"), Path("/tools/deno")))

    def test_local_video_cuts_copy_streams_and_audio_cuts_encode_mp3(self):
        cut = cut_arguments(self.job, Path("source.mp4"), Path("clip.mp4"))
        self.assertIn("copy", cut)
        self.assertEqual(cut[cut.index("-ss") + 1], "60")
        self.assertEqual(cut[cut.index("-t") + 1], "30")
        self.job.format, self.job.quality = "MP3", "128"
        audio = download_arguments(self.job, Path("/tools/ffmpeg"), Path("/tools/deno"))
        self.assertNotIn("-x", audio)
        self.assertIn("libmp3lame", cut_arguments(self.job, Path("source.m4a"), Path("clip.mp3")))

    def test_clip_progress_uses_relative_time_and_saved_bytes(self):
        for line in ("total_size=5242880", "out_time_us=15000000", "speed=2.0x", "progress=continue"):
            self.window.handle_line(line)
        self.assertEqual(self.job.status, "Dönüştürülüyor")
        self.assertEqual(self.job.progress, 91)
        self.assertEqual(self.window.progress_animation.endValue(), 91)
        self.assertIn("5.0 MB kaydedildi", self.window.status.text())
        self.assertIn("15.0 / 30.0 sn", self.window.status.text())
        self.assertIn("Kalan: 0:07", self.window.status.text())

    def test_unknown_values_and_completion_wait_for_verification(self):
        for line in ("total_size=N/A", "out_time_us=N/A", "speed=N/A", "progress=continue"):
            self.window.handle_line(line)
        self.assertEqual(self.job.status, "Dönüştürülüyor")
        self.assertEqual(self.window.progress.maximum(), 100)
        self.assertNotIn("Kalan:", self.window.status.text())
        for line in ("out_time_us=31000000", "progress=end"):
            self.window.handle_line(line)
        self.assertEqual(self.job.progress, 98)
        self.assertEqual(self.job.status, "Dönüştürülüyor")

    def test_progress_blocks_can_arrive_in_separate_reads(self):
        class Output:
            chunk = b"total_size=2097152\nout_time_us=7500"

            def readAllStandardOutput(self):
                return self.chunk

        output = Output()
        self.window.process = output
        try:
            self.window.read_output()
            output.chunk = b"000\nspeed=1x\nprogress=continue\n"
            self.window.read_output()
        finally:
            self.window.process = None
        self.assertEqual(self.job.progress, 88)
        self.assertIn("2.0 MB kaydedildi", self.window.status.text())

    def test_full_downloads_show_bytes_without_a_total(self):
        self.job.start_time = self.job.end_time = None
        self.window.handle_line('__PROGRESS__{"downloaded_bytes": 10485760, "total_bytes": 20971520}')
        self.assertEqual(self.job.progress, 45)
        self.assertIn("10.0 MB / 20.0 MB", self.window.status.text())
        self.window.handle_line('__PROGRESS__{"downloaded_bytes": 3145728}')
        self.assertIn("3.0 MB", self.window.status.text())
        self.assertEqual(self.window.progress.maximum(), 100)
        self.assertEqual(self.job.progress, 45)

    def test_separate_streams_and_stages_never_reset_progress(self):
        self.window.handle_line('__STREAMS__["video", "audio"]')
        values = []
        for line in (
            '__PROGRESS__{"filename":"video", "downloaded_bytes":50, "total_bytes":100}',
            '__PROGRESS__{"filename":"video", "status":"finished"}',
            '__PROGRESS__{"filename":"audio", "downloaded_bytes":10, "total_bytes":100}',
            '__PROGRESS__{"filename":"audio", "downloaded_bytes":5, "total_bytes_estimate":200}',
            '__PROGRESS__{"filename":"audio", "status":"finished"}',
            '__POST__{}',
            'out_time_us=15000000', 'progress=continue',
        ):
            self.window.handle_line(line)
            values.append(self.job.progress)
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[1], 37)
        self.assertEqual(values[4], 75)
        self.assertEqual(values[-1], 91)
        self.assertEqual(self.window.progress.maximum(), 100)

    def test_progress_animates_forward_and_only_new_job_resets(self):
        self.window.set_progress(0, reset=True)
        self.window.set_progress(80)
        QTest.qWait(100)
        self.assertGreater(self.window.progress.value(), 0)
        self.assertLess(self.window.progress.value(), 80)
        self.window.set_progress(20)
        self.assertEqual(self.job.progress, 80)
        QTest.qWait(250)
        self.assertEqual(self.window.progress.value(), 80)
        self.window.set_progress(0, reset=True)
        self.assertEqual(self.window.progress.value(), 0)

    def test_update_button_explains_current_activity(self):
        self.window.updater = object()
        try:
            self.window.update_message("Nightly güncellemeleri denetleniyor…")
            self.assertEqual(self.window.start.text(), "Güncellemeler denetleniyor…")
            self.window.update_message("İndirme motoru güncelleniyor: nightly")
            self.assertEqual(self.window.start.text(), "Güncelleniyor…")
        finally:
            self.window.updater = None

    def test_download_completion_starts_local_cut_before_verification(self):
        source = Path(self.directory.name) / "source.mp4"
        source.write_bytes(b"source fixture")
        self.job.output = str(source)
        with patch.object(self.window, "start_local_cut") as cut, patch.object(self.window, "verify_output") as verify:
            self.window.download_finished(0, None)
            cut.assert_called_once()
            verify.assert_not_called()

    def test_cancelled_clip_cleans_only_its_workspace(self):
        existing = Path(self.directory.name) / "existing.mp4"
        existing.write_bytes(b"keep existing output")
        self.window.clip_workspace = tempfile.TemporaryDirectory(dir=self.directory.name)
        workspace = Path(self.window.clip_workspace.name)
        (workspace / "source.mp4").write_bytes(b"temporary full source")
        self.window.cancelled = True
        self.window.finish_job(False, "cancelled")
        self.assertFalse(workspace.exists())
        self.assertEqual(existing.read_bytes(), b"keep existing output")
        self.assertEqual(self.job.status, "İptal edildi")

    def test_startup_removes_interrupted_downloads_but_keeps_other_files(self):
        root = self.window.downloads_dir
        interrupted = root / "job-interrupted"
        interrupted.mkdir()
        (interrupted / "source.mp4.part").write_bytes(b"interrupted download")
        unrelated = root / "keep.txt"
        unrelated.write_text("keep")
        self.assertEqual(cleanup_downloads(root), [])
        self.assertFalse(interrupted.exists())
        self.assertEqual(unrelated.read_text(), "keep")

    def test_full_downloads_also_use_the_settings_workspace(self):
        self.job.start_time = self.job.end_time = None
        self.job.status = "Bekliyor"
        self.window.current = None
        self.window.allow_old_for_batch = True
        self.window.engine = Path("/tools/yt-dlp")
        self.window.ffmpeg, self.window.deno = Path("/tools/ffmpeg"), Path("/tools/deno")
        with patch.object(self.window, "start_process") as start:
            self.window.next_job()
        workspace = Path(self.window.clip_workspace.name)
        self.assertEqual(workspace.parent, self.window.data_dir / "downloads")
        args = start.call_args.args[1]
        self.assertEqual(args[args.index("-P") + 1], str(workspace))
        self.window.cancelled = True
        self.window.finish_job(False, "cancelled")
        self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path

import pytest

from main import Job, download_arguments, engine_asset, load_jobs, parse_links, save_jobs


def test_batch_preserves_order_and_duplicates():
    first, second = "https://youtu.be/abc", "https://www.youtube.com/watch?v=def&list=123"
    assert parse_links(f"{first}\n{second}\n{first}") == [first, second, first]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://youtube.com.evil.org/a", "--exec bad", "https://user@youtube.com/a"])
def test_rejects_non_youtube_links(url):
    with pytest.raises(ValueError):
        parse_links(url)


def test_settings_are_captured_in_each_job_and_restored_in_order(tmp_path):
    jobs = [Job("https://youtu.be/a", "MP3", "128", "/tmp/a", status="Dönüştürülüyor"),
            Job("https://youtu.be/b", "MP4", "best", "/tmp/b", status="Tamamlandı")]
    queue = tmp_path / "queue.json"
    save_jobs(queue, jobs)
    restored = load_jobs(queue)
    assert [j.url for j in restored] == [j.url for j in jobs]
    assert restored[0].status == "Bekliyor"
    assert restored[0].quality == "128"
    assert restored[1].status == "Tamamlandı"


def test_mp4_best_does_not_limit_resolution():
    job = Job("https://youtu.be/a", "MP4", "best", "/tmp/videos")
    args = download_arguments(job, Path("/bin/ffmpeg"), Path("/bin/deno"))
    assert args[args.index("-f") + 1] == "bv*+ba/b"
    assert args[args.index("--merge-output-format") + 1] == "mp4"
    assert "--no-playlist" in args
    assert args[-2:] == ["--", job.url]


def test_resolution_cap_and_mp3_bitrate():
    args = download_arguments(Job("https://youtu.be/a", "MP4", "1080", "/tmp"), Path("/bin/ffmpeg"), Path("/bin/deno"))
    assert args[args.index("-f") + 1] == "bv*[height<=1080]+ba/b[height<=1080]"
    args = download_arguments(Job("https://youtu.be/a", "MP3", "128", "/tmp"), Path("/bin/ffmpeg"), Path("/bin/deno"))
    assert args[args.index("--audio-quality") + 1] == "128K"
    assert args[args.index("--audio-format") + 1] == "mp3"


def test_assets():
    assert engine_asset("darwin", "arm64") == "yt-dlp_macos"
    assert engine_asset("win32", "arm64") == "yt-dlp_arm64.exe"
    assert engine_asset("win32", "AMD64") == "yt-dlp.exe"


def test_file_settings_persist_geometry_and_preferences(tmp_path):
    from main import StateManager
    settings = StateManager(tmp_path)
    settings.setValue('folder', '/videos')
    settings.setValue('quality', '320')
    settings.setValue('geometry', b'\x00\xff\x10')
    restored = StateManager(tmp_path)
    assert restored.value('folder') == '/videos'
    assert restored.value('quality') == '320'
    assert restored.value('geometry') == b'\x00\xff\x10'
    assert not (tmp_path / 'settings.tmp').exists()


def test_corrupt_settings_recover(tmp_path):
    from main import StateManager
    (tmp_path / 'settings.json').write_text('invalid json')
    assert StateManager(tmp_path).value('format', 'MP4') == 'MP4'
    (tmp_path / 'settings.json').write_text('[]')
    assert StateManager(tmp_path).value('format', 'MP4') == 'MP4'


def test_application_data_paths(monkeypatch, tmp_path):
    import main
    monkeypatch.delenv('YTINDIR_DATA_DIR', raising=False)
    monkeypatch.setattr(main.sys, 'platform', 'win32')
    monkeypatch.setenv('APPDATA', str(tmp_path))
    assert main.application_data_dir() == tmp_path / 'YTIndir'
    monkeypatch.setattr(main.sys, 'platform', 'darwin')
    assert main.application_data_dir() == Path.home() / 'Library/Application Support/YTIndir'
    monkeypatch.setenv('YTINDIR_DATA_DIR', str(tmp_path / 'portable'))
    assert main.application_data_dir() == tmp_path / 'portable'

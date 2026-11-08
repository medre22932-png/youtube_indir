"""Optional live YouTube check using a short public video (or a supplied URL)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Job, download_arguments, find_tool, run_tool
from main import EngineUpdater


root, outputs = Path(sys.argv[1]), Path(sys.argv[2])
outputs.mkdir(parents=True, exist_ok=True)
updater = EngineUpdater(root / "engine")
engine = updater.current()
updater.close()
url = sys.argv[3] if len(sys.argv) > 3 else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
for format, quality in (("MP4", "best"), ("MP3", "128")):
    job = Job(url, format, quality, str(outputs))
    args = download_arguments(job, find_tool("ffmpeg", root), find_tool("deno", root))
    args[-2:-2] = ["--socket-timeout", "10", "--retries", "0", "--extractor-retries", "0"]
    print(run_tool([str(engine), *args], timeout=120), flush=True)

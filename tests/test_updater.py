import hashlib
import json
from pathlib import Path

import pgpy
import pytest
from pgpy.constants import HashAlgorithm, KeyFlags, PubKeyAlgorithm

import main as module
from main import engine_asset
from main import EngineUpdater, manifest_digest, verify_manifest


@pytest.fixture(scope="module")
def signing_key(tmp_path_factory):
    key = pgpy.PGPKey.new(PubKeyAlgorithm.RSAEncryptOrSign, 2048)
    uid = pgpy.PGPUID.new("Test signing key")
    key.add_uid(uid, usage={KeyFlags.Sign}, hashes=[HashAlgorithm.SHA256])
    path = tmp_path_factory.mktemp("key") / "public.key"
    path.write_text(str(key.pubkey))
    return key, path


def test_signature_and_tampering(signing_key):
    key, path = signing_key
    content = b"a" * 64 + b"  yt-dlp_macos\n"
    signature = bytes(key.sign(content, detached=True))
    verify_manifest(content, signature, path)
    with pytest.raises(RuntimeError, match="imza"):
        verify_manifest(content + b"tampered", signature, path)


def test_manifest_exact_asset():
    manifest = b"a" * 64 + b"  yt-dlp_macos\n" + b"b" * 64 + b"  yt-dlp.exe\n"
    assert manifest_digest(manifest, "yt-dlp.exe") == "b" * 64
    with pytest.raises(RuntimeError):
        manifest_digest(manifest, "yt-dlp")


def fake_release(tag):
    name = engine_asset()
    return {"tag_name": tag, "assets": [{"name": name, "browser_download_url":
            f"https://github.com/{module.REPOSITORY}/releases/download/{tag}/{name}"}]}


def prepare_updater(tmp_path, monkeypatch, *, broken=False):
    updater = EngineUpdater(tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda *a: None)
    monkeypatch.setattr(module, "run_tool", lambda args: "2026.11.08")
    monkeypatch.setattr(updater, "release", lambda repo: fake_release("2026.11.08"))
    content = b"engine executable"
    manifest = f"{hashlib.sha256(content).hexdigest()}  {engine_asset()}\n".encode()
    monkeypatch.setattr(updater, "fetch", lambda url: manifest if url.endswith("SUMS") else b"signature")
    def download(url, path, digest):
        if broken:
            path.write_bytes(b"incomplete")
            raise RuntimeError("network interruption")
        path.write_bytes(content)
    monkeypatch.setattr(updater, "download", download)
    return updater


def test_first_install_and_no_redownload_when_current(tmp_path, monkeypatch):
    updater = prepare_updater(tmp_path, monkeypatch)
    path, version = updater.update()
    assert path.is_file()
    assert updater.current() == path
    monkeypatch.setattr(updater, "download", lambda *args: pytest.fail("Should not re-download a valid current engine"))
    assert updater.update()[0] == path
    updater.close()


def test_interrupted_update_keeps_active_pointer(tmp_path, monkeypatch):
    previous = tmp_path / "2026.11.01" / engine_asset()
    previous.parent.mkdir()
    previous.write_bytes(b"previous engine")
    pointer = tmp_path / "engine.json"
    pointer.write_text(json.dumps({"directory": "2026.11.01"}))
    original = pointer.read_bytes()
    updater = prepare_updater(tmp_path, monkeypatch, broken=True)
    with pytest.raises(RuntimeError, match="interruption"):
        updater.update()
    assert pointer.read_bytes() == original
    assert updater.current() == previous
    assert previous.read_bytes() == b"previous engine"
    updater.close()


def test_failed_smoke_check_keeps_previous_pointer(tmp_path, monkeypatch):
    updater = prepare_updater(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "run_tool", lambda args: (_ for _ in ()).throw(RuntimeError("broken binary")))
    with pytest.raises(RuntimeError, match="broken binary"):
        updater.update()
    assert not (tmp_path / "engine.json").exists()
    updater.close()


def test_download_rejects_bad_hash(tmp_path):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): return iter([b"wrong bytes"])
    updater = EngineUpdater(tmp_path)
    updater.session.get = lambda *args, **kwargs: Response()
    with pytest.raises(RuntimeError, match="SHA-256"):
        updater.download("https://example.invalid", tmp_path / "new", "a" * 64)
    updater.close()

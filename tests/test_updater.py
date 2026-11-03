"""The updater's logic, with GitHub replaced by fake responses (no network)."""
import hashlib
import io
import json

import pytest

from khmerpdf import updater
from khmerpdf.updater import Release, UpdateError

PAYLOAD = b"new installer bytes"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_get(routes: dict):
    def get(url, accept=None):
        if url not in routes:
            raise OSError(f"no route {url}")
        body = routes[url]
        return FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode())
    return get


@pytest.mark.parametrize("latest, current, newer", [
    ("0.5.6", "0.5.5", True), ("v0.6.0", "0.5.10", True), ("0.5.10", "0.5.9", True),
    ("0.5.5", "0.5.5", False), ("0.5.4", "0.5.5", False), ("0.6.0-beta", "0.6.0", False), ("0.6.0", "0.6.0-beta", True),
])
def test_is_newer(latest, current, newer):
    assert updater.is_newer(latest, current) is newer


def test_asset_names_match_the_release_files():
    assert updater.asset_name("windows", "0.5.5") == "KhmerPDF-Setup-0.5.5.exe"
    assert updater.asset_name("appimage", "0.5.5") == "KhmerPDF-0.5.5-x86_64.AppImage"
    assert updater.asset_name("deb", "0.5.5") == "khmerpdf_0.5.5_amd64.deb"


def test_running_from_source_cannot_update_itself():
    assert updater.install_kind() is None


def test_fetch_latest(monkeypatch):
    monkeypatch.setattr(updater, "_get", fake_get({updater.LATEST_API: {
        "tag_name": "v0.6.0", "body": "What's new", "html_url": "https://example/rel",
        "assets": [{"name": "KhmerPDF-Setup-0.6.0.exe", "browser_download_url": "https://example/setup.exe", "size": 5,
                    "digest": f"sha256:{SHA}"}]}}))
    r = updater.fetch_latest()
    assert (r.version, r.notes, r.page) == ("0.6.0", "What's new", "https://example/rel")
    assert r.assets["KhmerPDF-Setup-0.6.0.exe"]["digest"] == f"sha256:{SHA}"


def test_fetch_latest_offline_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(updater, "_get", fake_get({}))
    with pytest.raises(UpdateError, match="Could not check for updates"):
        updater.fetch_latest()


def test_checksum_from_github_digest_or_sums_file(monkeypatch):
    name = "khmerpdf_0.6.0_amd64.deb"
    with_digest = Release("0.6.0", "", "", {name: {"url": "u", "digest": f"sha256:{SHA.upper()}"}})
    assert updater.expected_sha256(with_digest, name) == SHA
    monkeypatch.setattr(updater, "_get", fake_get({"https://example/sums": f"{'0' * 64}  other.deb\n{SHA}  {name}\n".encode()}))
    with_sums = Release("0.6.0", "", "", {name: {"url": "u", "digest": None},
                                          "SHA256SUMS-linux.txt": {"url": "https://example/sums"}})
    assert updater.expected_sha256(with_sums, name) == SHA
    assert updater.expected_sha256(Release("0.6.0", "", "", {name: {"url": "u"}}), name) is None


def test_prepare_downloads_and_verifies(monkeypatch, tmp_path):
    name = "KhmerPDF-Setup-0.6.0.exe"
    monkeypatch.setattr(updater, "_get", fake_get({"https://example/setup.exe": PAYLOAD}))
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    release = Release("0.6.0", "", "", {name: {"url": "https://example/setup.exe", "digest": f"sha256:{SHA}"}})
    file = updater.prepare(release, "windows")
    assert file.read_bytes() == PAYLOAD


def test_prepare_refuses_a_mismatch_and_deletes_it(monkeypatch, tmp_path):
    name = "KhmerPDF-Setup-0.6.0.exe"
    monkeypatch.setattr(updater, "_get", fake_get({"https://example/setup.exe": b"tampered"}))
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    release = Release("0.6.0", "", "", {name: {"url": "https://example/setup.exe", "digest": f"sha256:{SHA}"}})
    with pytest.raises(UpdateError, match="doesn't match its checksum"):
        updater.prepare(release, "windows")
    assert not list(tmp_path.iterdir())


def test_prepare_refuses_without_a_checksum(monkeypatch):
    name = "KhmerPDF-Setup-0.6.0.exe"
    release = Release("0.6.0", "", "", {name: {"url": "https://example/setup.exe", "digest": None}})
    with pytest.raises(UpdateError, match="can't be installed safely"):
        updater.prepare(release, "windows")


def test_cancelled_download_leaves_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_get", fake_get({"https://example/f": PAYLOAD}))
    dest = tmp_path / "f"
    with pytest.raises(UpdateError, match="cancelled"):
        updater.download("https://example/f", dest, progress=lambda done, total: False)
    assert not dest.exists()

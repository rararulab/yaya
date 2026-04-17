"""Tests for updater.apply_update and supporting helpers."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from yaya.core import updater
from yaya.core.updater import UpdateResult

pytestmark = pytest.mark.unit


def _build_tarball(binary_bytes: bytes) -> tuple[bytes, str]:
    """Return (tarball_bytes, sha256_hex) containing a single file named 'yaya'."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = io.BytesIO(binary_bytes)
        info = tarfile.TarInfo(name="yaya")
        info.size = len(binary_bytes)
        info.mode = 0o755
        tar.addfile(info, data)
    blob = buf.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


@pytest.fixture
def fake_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = tmp_path / "yaya-bin"
    exe.write_bytes(b"old binary\n")
    exe.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(exe))
    return exe


def _register_assets(httpx_mock: HTTPXMock, target: str, version: str, tarball: bytes, sha: str) -> None:
    base = f"https://github.com/{updater.REPO}/releases/download/{version}"
    archive_url = f"{base}/yaya-{version}-{target}.tar.gz"
    httpx_mock.add_response(url=archive_url, content=tarball)
    httpx_mock.add_response(url=f"{archive_url}.sha256", text=f"{sha}  yaya-{version}-{target}.tar.gz\n")


def test_apply_update_happy_path(httpx_mock: HTTPXMock, fake_executable: Path) -> None:
    tarball, sha = _build_tarball(b"new binary payload\n")
    _register_assets(httpx_mock, "x86_64-unknown-linux-gnu", "9.9.9", tarball, sha)

    with updater.new_http_client() as client:
        status = updater.apply_update(client, "x86_64-unknown-linux-gnu", "9.9.9")

    assert status.result == UpdateResult.UPDATED
    assert status.latest_version == "9.9.9"
    assert fake_executable.read_bytes() == b"new binary payload\n"


def test_apply_update_checksum_mismatch(httpx_mock: HTTPXMock, fake_executable: Path) -> None:
    tarball, _ = _build_tarball(b"payload\n")
    _register_assets(
        httpx_mock,
        "x86_64-unknown-linux-gnu",
        "9.9.9",
        tarball,
        sha="0" * 64,  # wrong
    )

    with updater.new_http_client() as client:
        status = updater.apply_update(client, "x86_64-unknown-linux-gnu", "9.9.9")

    assert status.result == UpdateResult.FAILED
    assert "checksum" in status.message.lower()
    assert fake_executable.read_bytes() == b"old binary\n"  # not replaced


def test_apply_update_download_failure(httpx_mock: HTTPXMock, fake_executable: Path) -> None:
    base = f"https://github.com/{updater.REPO}/releases/download/9.9.9"
    httpx_mock.add_response(url=f"{base}/yaya-9.9.9-x86_64-unknown-linux-gnu.tar.gz", status_code=404)

    with updater.new_http_client() as client:
        status = updater.apply_update(client, "x86_64-unknown-linux-gnu", "9.9.9")

    assert status.result == UpdateResult.FAILED
    assert "download failed" in status.message.lower()


def test_apply_update_missing_binary_in_archive(httpx_mock: HTTPXMock, fake_executable: Path) -> None:
    # Tarball that does not contain a file named 'yaya'
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = io.BytesIO(b"decoy")
        info = tarfile.TarInfo(name="not-yaya")
        info.size = len(b"decoy")
        tar.addfile(info, data)
    tarball = buf.getvalue()
    sha = hashlib.sha256(tarball).hexdigest()
    _register_assets(httpx_mock, "x86_64-unknown-linux-gnu", "9.9.9", tarball, sha)

    with updater.new_http_client() as client:
        status = updater.apply_update(client, "x86_64-unknown-linux-gnu", "9.9.9")

    assert status.result == UpdateResult.FAILED
    assert "not found" in status.message.lower()


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"yaya rocks" * 1000
    f = tmp_path / "blob.bin"
    f.write_bytes(payload)
    assert updater._sha256_file(f) == hashlib.sha256(payload).hexdigest()


def test_cached_latest_missing_file_returns_none(tmp_path: Path) -> None:
    # state dir is isolated to tmp_path by the autouse fixture; nothing written
    assert updater.read_cached_latest() is None


def test_cached_latest_corrupt_file(tmp_path: Path) -> None:
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LATEST_VERSION_FILE.write_text("not json", encoding="utf-8")
    assert updater.read_cached_latest() is None


def test_is_skipped_missing_file() -> None:
    assert updater.is_skipped("1.2.3") is False


def test_spawn_background_refresh_does_not_raise(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=updater.RELEASES_API, json={"tag_name": "1.2.3"})
    updater.spawn_background_refresh()
    # Give the thread a moment to run; joining is not part of the public API,
    # but finishing without exception is enough for coverage.
    import threading

    for t in threading.enumerate():
        if t.name == "yaya-update-check":
            t.join(timeout=2.0)


def test_upgrade_hint_non_frozen() -> None:
    # is_frozen() is False in tests
    hint = updater.upgrade_hint()
    assert "uv tool upgrade" in hint or "pip install" in hint


def test_shape_docstring_of_update_status() -> None:
    # Smoke: to_dict contains the canonical fields
    status = updater.UpdateStatus(
        result=UpdateResult.UP_TO_DATE,
        current_version="1.0.0",
        latest_version="1.0.0",
        message="ok",
    )
    d = status.to_dict()
    assert d["result"] == "UP_TO_DATE"
    assert d["current_version"] == "1.0.0"

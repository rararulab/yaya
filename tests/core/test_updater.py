from __future__ import annotations

from pytest_httpx import HTTPXMock

from yaya.core import updater
from yaya.core.updater import UpdateResult


def test_semver_tuple() -> None:
    assert updater.semver_tuple("1.2.3") == (1, 2, 3)
    assert updater.semver_tuple("v1.2.3") == (1, 2, 3)
    assert updater.semver_tuple("1.2") == (1, 2, 0)
    assert updater.semver_tuple("0.0.1-dev") == (0, 0, 1)
    assert updater.semver_tuple("garbage") == (0, 0, 0)


def test_semver_ordering() -> None:
    assert updater.semver_tuple("0.0.1") < updater.semver_tuple("0.1.0")
    assert updater.semver_tuple("0.1.0") < updater.semver_tuple("1.0.0")


def test_detect_target_is_known_or_none() -> None:
    target = updater.detect_target()
    assert target is None or target in {
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
        "aarch64-apple-darwin",
        "x86_64-pc-windows-msvc",
    }


def test_is_frozen_false_in_tests() -> None:
    assert updater.is_frozen() is False


def test_fetch_latest_version_happy(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=updater.RELEASES_API, json={"tag_name": "1.2.3"})
    with updater.new_http_client() as client:
        assert updater.fetch_latest_version(client) == "1.2.3"


def test_fetch_latest_version_caches(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=updater.RELEASES_API, json={"tag_name": "1.2.3"})
    with updater.new_http_client() as client:
        updater.fetch_latest_version(client)
    cached = updater.read_cached_latest()
    assert cached is not None
    assert cached[0] == "1.2.3"


def test_fetch_latest_version_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=updater.RELEASES_API, status_code=500)
    with updater.new_http_client() as client:
        assert updater.fetch_latest_version(client) is None


def test_check_for_updates_up_to_date(httpx_mock: HTTPXMock) -> None:
    from yaya import __version__

    httpx_mock.add_response(url=updater.RELEASES_API, json={"tag_name": __version__})
    with updater.new_http_client() as client:
        status = updater.check_for_updates(client)
    assert status.result == UpdateResult.UP_TO_DATE
    assert status.latest_version == __version__


def test_check_for_updates_available(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=updater.RELEASES_API, json={"tag_name": "999.0.0"})
    with updater.new_http_client() as client:
        status = updater.check_for_updates(client)
    assert status.result == UpdateResult.UPDATE_AVAILABLE
    assert status.latest_version == "999.0.0"


def test_skip_version_roundtrip() -> None:
    assert not updater.is_skipped("1.2.3")
    updater.skip_version("1.2.3")
    assert updater.is_skipped("1.2.3")
    assert not updater.is_skipped("1.2.4")

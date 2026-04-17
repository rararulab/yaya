"""Self-update core: network + filesystem logic with no CLI dependencies.

This module returns structured results. Presentation (text vs JSON, colors,
progress) lives in the CLI layer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

import httpx

from yaya import __version__

REPO = "rararulab/yaya"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DEFAULT_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 600.0
CACHE_TTL_SECONDS = 24 * 3600

STATE_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "yaya"
LATEST_VERSION_FILE = STATE_DIR / "latest_version.json"
SKIPPED_VERSION_FILE = STATE_DIR / "skipped_version.txt"


class UpdateResult(Enum):
    UPDATE_AVAILABLE = auto()
    UPDATED = auto()
    UP_TO_DATE = auto()
    FAILED = auto()
    UNSUPPORTED = auto()


@dataclass(frozen=True)
class UpdateStatus:
    result: UpdateResult
    current_version: str
    latest_version: str | None = None
    message: str = ""
    extra: dict[str, str] = field(default_factory=lambda: {})

    def to_dict(self) -> dict[str, object]:
        return {
            "result": self.result.name,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "message": self.message,
            **self.extra,
        }


def semver_tuple(version: str) -> tuple[int, int, int]:
    v = version.strip().lstrip("v")
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", v)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def detect_target() -> str | None:
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "AMD64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    arch = arch_map.get(platform.machine())
    if not arch:
        return None
    sys_name = platform.system()
    if sys_name == "Darwin" and arch == "aarch64":
        return "aarch64-apple-darwin"
    if sys_name == "Linux":
        return f"{arch}-unknown-linux-gnu"
    if sys_name == "Windows" and arch == "x86_64":
        return "x86_64-pc-windows-msvc"
    return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def upgrade_hint() -> str:
    if is_frozen():
        return "yaya update"
    return "uv tool upgrade yaya  # or: pip install -U yaya"


def new_http_client() -> httpx.Client:
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": f"yaya/{__version__}"},
    )


def fetch_latest_version(client: httpx.Client) -> str | None:
    try:
        r = client.get(RELEASES_API)
        r.raise_for_status()
        tag = r.json().get("tag_name")
        if isinstance(tag, str) and tag:
            _cache_latest(tag)
            return tag
    except httpx.HTTPError, ValueError, json.JSONDecodeError:
        return None
    return None


def _cache_latest(version: str) -> None:
    with contextlib.suppress(OSError):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LATEST_VERSION_FILE.write_text(
            json.dumps({"version": version, "checked_at": int(time.time())}),
            encoding="utf-8",
        )


def read_cached_latest() -> tuple[str, int] | None:
    try:
        data = json.loads(LATEST_VERSION_FILE.read_text(encoding="utf-8"))
        return str(data["version"]), int(data["checked_at"])
    except OSError, json.JSONDecodeError, KeyError, ValueError:
        return None


def is_skipped(version: str) -> bool:
    try:
        return SKIPPED_VERSION_FILE.read_text(encoding="utf-8").strip() == version
    except OSError:
        return False


def skip_version(version: str) -> None:
    with contextlib.suppress(OSError):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SKIPPED_VERSION_FILE.write_text(version, encoding="utf-8")


def spawn_background_refresh() -> None:
    def _refresh() -> None:
        with contextlib.suppress(Exception), new_http_client() as client:
            fetch_latest_version(client)

    t = threading.Thread(target=_refresh, name="yaya-update-check", daemon=True)
    t.start()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def check_for_updates(client: httpx.Client) -> UpdateStatus:
    """Query the latest release and classify the result."""
    latest = fetch_latest_version(client)
    if not latest:
        return UpdateStatus(
            result=UpdateResult.FAILED,
            current_version=__version__,
            message="Failed to query the latest release.",
        )
    if semver_tuple(latest) <= semver_tuple(__version__):
        return UpdateStatus(
            result=UpdateResult.UP_TO_DATE,
            current_version=__version__,
            latest_version=latest,
            message=f"Already up to date ({__version__}).",
        )
    return UpdateStatus(
        result=UpdateResult.UPDATE_AVAILABLE,
        current_version=__version__,
        latest_version=latest,
        message=f"Update available: {latest}",
    )


def _download_archive(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)


def apply_update(client: httpx.Client, target: str, latest: str) -> UpdateStatus:
    """Download the release asset for `target`, verify, and replace the binary."""
    archive_name = f"yaya-{latest}-{target}.tar.gz"
    base = f"https://github.com/{REPO}/releases/download/{latest}"
    archive_url = f"{base}/{archive_name}"
    sha_url = f"{archive_url}.sha256"

    with tempfile.TemporaryDirectory(prefix="yaya-update-") as tmp:
        tmpdir = Path(tmp)
        archive_path = tmpdir / archive_name
        try:
            _download_archive(client, archive_url, archive_path)
            sha_line = client.get(sha_url).text.strip().split()
            sha_expected = sha_line[0] if sha_line else ""
        except httpx.HTTPError as exc:
            return UpdateStatus(
                result=UpdateResult.FAILED,
                current_version=__version__,
                latest_version=latest,
                message=f"Download failed: {exc}",
            )

        if _sha256_file(archive_path) != sha_expected:
            return UpdateStatus(
                result=UpdateResult.FAILED,
                current_version=__version__,
                latest_version=latest,
                message="Checksum mismatch; aborting.",
            )

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmpdir, filter="data")
        except (tarfile.TarError, OSError) as exc:
            return UpdateStatus(
                result=UpdateResult.FAILED,
                current_version=__version__,
                latest_version=latest,
                message=f"Failed to extract archive: {exc}",
            )

        new_binary = tmpdir / "yaya"
        if not new_binary.exists():
            return UpdateStatus(
                result=UpdateResult.FAILED,
                current_version=__version__,
                latest_version=latest,
                message="Binary 'yaya' not found in archive.",
            )

        current = Path(sys.executable).resolve()
        try:
            shutil.copy2(new_binary, current)
            current.chmod(current.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError as exc:
            return UpdateStatus(
                result=UpdateResult.FAILED,
                current_version=__version__,
                latest_version=latest,
                message=f"Failed to install to {current}: {exc}",
                extra={"target_path": str(current)},
            )

    return UpdateStatus(
        result=UpdateResult.UPDATED,
        current_version=__version__,
        latest_version=latest,
        message=f"Updated to {latest}.",
    )

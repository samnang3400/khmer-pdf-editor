"""Updates from the project's GitHub Releases.

Checks the latest release, picks the file that matches how this copy was installed (the Windows setup, the
AppImage or the .deb), downloads it, checks its SHA-256 against the one GitHub reports for the file (or the
release's SHA256SUMS file), and installs it. A copy that can't update itself (the .tar.gz, or running from
source) is pointed to the download page instead. Nothing is installed without a checksum that matches.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "samnang3400/khmer-pdf-editor"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 20


class UpdateError(Exception):
    """Something the user should read: no network, a bad download, an install that failed."""


@dataclass
class Release:
    version: str
    notes: str
    page: str
    assets: dict[str, dict] = field(default_factory=dict)   # name -> {"url", "size", "digest"}


def version_key(version: str) -> tuple:
    """'v0.5.10' -> (0, 5, 10). Anything after the numbers (e.g. '-beta') sorts before the release itself."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)", version.strip())
    if not m:
        return (0,)
    numbers = tuple(int(n) for n in m.group(1).split("."))
    return numbers + ((0,) if m.group(2) else (1,))


def is_newer(latest: str, current: str = __version__) -> bool:
    return version_key(latest) > version_key(current)


def _get(url: str, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"KhmerPDF/{__version__}"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def fetch_latest() -> Release:
    try:
        with _get(LATEST_API) as response:
            data = json.load(response)
    except Exception as ex:
        raise UpdateError(f"Could not check for updates: {ex}") from ex
    assets = {a["name"]: {"url": a["browser_download_url"], "size": a.get("size") or 0, "digest": a.get("digest")}
              for a in data.get("assets") or [] if a.get("name") and a.get("browser_download_url")}
    return Release(version=str(data.get("tag_name") or "").lstrip("v"), notes=str(data.get("body") or "").strip(),
                   page=str(data.get("html_url") or RELEASES_PAGE), assets=assets)


# ---------- how this copy was installed ----------

def install_kind() -> str | None:
    """'windows' (the setup), 'appimage', 'deb', or None when it can't update itself (.tar.gz, source)."""
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if Path(sys.executable).resolve().is_relative_to("/opt/khmerpdf"):
        return "deb"
    return None


def asset_name(kind: str, version: str) -> str:
    return {"windows": f"KhmerPDF-Setup-{version}.exe",
            "appimage": f"KhmerPDF-{version}-x86_64.AppImage",
            "deb": f"khmerpdf_{version}_amd64.deb"}[kind]


def expected_sha256(release: Release, name: str) -> str | None:
    """The file's SHA-256 as GitHub reports it, or as listed in the release's SHA256SUMS file(s)."""
    digest = (release.assets.get(name) or {}).get("digest") or ""
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    for sums in (n for n in release.assets if n.upper().startswith("SHA256SUMS")):
        try:
            with _get(release.assets[sums]["url"], accept="application/octet-stream") as response:
                text = response.read(1_000_000).decode("utf-8", "replace")
        except Exception:
            continue
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
    return None


# ---------- download and install ----------

def download(url: str, dest: Path, progress: Callable[[int, int], bool] | None = None) -> str:
    """Downloads to dest; returns its SHA-256. progress(done, total) returning False cancels."""
    digest = hashlib.sha256()
    try:
        with _get(url, accept="application/octet-stream") as response, open(dest, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and progress(done, total) is False:
                    raise UpdateError("Download cancelled.")
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as ex:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {ex}") from ex
    return digest.hexdigest()


def prepare(release: Release, kind: str, progress: Callable[[int, int], bool] | None = None) -> Path:
    """Downloads this copy's file for the release and checks it. Returns the verified file."""
    name = asset_name(kind, release.version)
    asset = release.assets.get(name)
    if not asset:
        raise UpdateError(f"The release has no {name}.")
    expected = expected_sha256(release, name)
    if not expected:
        raise UpdateError("The release doesn't say what the file's checksum should be, so it can't be installed safely. "
                          "Download it from the release page instead.")
    folder = Path(os.environ["APPIMAGE"]).parent if kind == "appimage" else Path(tempfile.mkdtemp(prefix="khmerpdf-update-"))
    dest = folder / (f".{name}.part" if kind == "appimage" else name)
    actual = download(asset["url"], dest, progress)
    if actual != expected:
        dest.unlink(missing_ok=True)
        raise UpdateError("The downloaded file doesn't match its checksum, so it was not installed. Try again later.")
    return dest


def _ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install(kind: str, file: Path) -> None:
    """Starts the install of a verified file. For Windows and the AppImage the app must quit right after;
    the new version starts by itself. For the .deb this waits for the password prompt and apt."""
    if kind == "windows":
        app = Path(sys.executable)
        program_files = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
        all_users = any(p and str(app).lower().startswith(p.lower() + "\\") for p in program_files)
        args = "'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS'," + ("'/ALLUSERS'" if all_users else "'/CURRENTUSER'")
        # Wait for this app to close, run the setup in the same mode as the current install (all users needs the
        # one admin prompt), then start the new version
        script = (f"Start-Sleep -Seconds 2; "
                  f"$p = Start-Process -FilePath {_ps(str(file))} -ArgumentList {args} -Wait -PassThru"
                  f"{' -Verb RunAs' if all_users else ''}; "
                  f"if ($p.ExitCode -eq 0) {{ Start-Process -FilePath {_ps(str(app))} }}")
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    elif kind == "appimage":
        target = Path(os.environ["APPIMAGE"])
        file.chmod(0o755)
        try:
            os.replace(file, target)
        except OSError as ex:
            raise UpdateError(f"Could not replace {target}: {ex}. The new version is at {file}.") from ex
        subprocess.Popen([str(target)], start_new_session=True)
    elif kind == "deb":
        if not shutil.which("pkexec"):
            raise UpdateError(f"Install it with:  sudo apt install {file}")
        result = subprocess.run(["pkexec", "apt-get", "install", "-y", str(file)], capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr.strip().splitlines() or ["cancelled"])[-1]
            raise UpdateError(f"The update was not installed: {detail}")
        subprocess.Popen(["/opt/khmerpdf/KhmerPDF"], start_new_session=True)
    else:
        raise UpdateError("This copy can't update itself.")

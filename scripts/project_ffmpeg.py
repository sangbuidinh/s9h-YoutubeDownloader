"""Pinned source/build contract for the project FFmpeg runtime (not authorization)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
from urllib.parse import parse_qsl, urlsplit
import zipfile

FFMPEG_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"
LAME_SOURCE_URL = "https://sourceforge.net/projects/lame/files/lame/3.100/lame-3.100.tar.gz/download"
LAME_ROUTING_PATH = "/projects/lame/files/lame/3.100/lame-3.100.tar.gz/download"
LAME_PROJECT_PATH = "/project/lame/lame/3.100/lame-3.100.tar.gz"
INPUTS = {
    "ffmpeg": {
        "filename": f"ffmpeg-{FFMPEG_COMMIT}.tar.gz",
        "url": f"https://github.com/FFmpeg/FFmpeg/archive/{FFMPEG_COMMIT}.tar.gz",
        "size": 16894057,
        "sha256": "2ae7e42343cfffb811d15cfe98b6d005f082595fcdf034d30a4ff90cfed9f9c6",
        "redirect_hosts": ["codeload.github.com"],
    },
    "lame": {
        "filename": "lame-3.100.tar.gz",
        "url": LAME_SOURCE_URL,
        "size": 1524133,
        "sha256": "ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e",
        "redirect_hosts": [],
    },
    "w64devkit": {
        "filename": "w64devkit-x64-2.9.1.7z.exe",
        "url": "https://github.com/skeeto/w64devkit/releases/download/v2.9.1/w64devkit-x64-2.9.1.7z.exe",
        "size": 61462208,
        "sha256": "9208c19755cd4964b7915b9afcf02c66d493a4c870c4b3e83f6c538d9c1237a5",
        "redirect_hosts": ["release-assets.githubusercontent.com"],
    },
    "nasm": {
        "filename": "nasm-2.16.03-win64.zip",
        "url": "https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/win64/nasm-2.16.03-win64.zip",
        "size": 513543,
        "sha256": "3ee4782247bcb874378d02f7eab4e294a84d3d15f3f6ee2de2f47a46aa7226e6",
        "redirect_hosts": [],
    },
}
CONFIGURE = [
    "--target-os=mingw32", "--arch=x86_64",
    "--disable-autodetect", "--enable-libmp3lame", "--enable-schannel",
    "--disable-shared", "--enable-static", "--disable-ffplay", "--disable-doc",
    "--disable-debug", "--extra-version=s9h-minimal-1",
    "--extra-cflags=-I../../prefix/include",
    "--extra-ldflags=-L../../prefix/lib -static -Wl,--no-insert-timestamp",
]
# SChannel is the Windows TLS API, not a separately distributed media library.
EXTERNAL_LIBRARIES = ["libmp3lame", "schannel"]
SYSTEM_DLLS = {
    "advapi32.dll", "bcrypt.dll", "crypt32.dll", "gdi32.dll", "kernel32.dll",
    "msvcrt.dll", "ncrypt.dll", "ole32.dll", "psapi.dll", "secur32.dll",
    "shell32.dll", "user32.dll", "ws2_32.dll", "oleaut32.dll", "shlwapi.dll", "avicap32.dll",
}
REQUIRED_COMPONENTS = {
    "encoders": {"libmp3lame"},
    "decoders": {"h264", "aac", "opus", "vorbis", "mp3"},
    "demuxers": {"mov", "matroska", "mpegts", "mp3", "ogg", "wav", "hls"},
    "muxers": {"mp4", "ipod", "mp3", "mpegts", "matroska", "ogg", "wav"},
    "bsfs": {"aac_adtstoasc", "setts"},
    "protocols": {"file", "pipe", "http", "https", "tcp", "tls", "crypto", "data"},
}
TRANSIENT_HTTP_STATUSES = {"408", "425", "429", "500", "502", "503", "504"}
REDIRECT_HTTP_STATUSES = {"301", "302", "303", "307", "308"}
HTTP_RETRY_DELAYS = (2, 4, 8)
LAME_MAX_REDIRECTS = 4
LAME_MAX_URL_BYTES = 4096
LAME_MAX_QUERY_BYTES = 2048
LAME_MIRROR_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.dl\.sourceforge\.net$", re.IGNORECASE
)


class ProjectFFmpegError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ProjectFFmpegError(message)


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def file_identity(path: Path) -> dict:
    return {"filename": path.name, "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_input(path: Path, key: str) -> None:
    require(key in INPUTS, "unknown build input")
    pin = INPUTS[key]
    require(path.is_file() and not path.is_symlink(), f"missing regular input: {key}")
    require(file_identity(path) == {k: pin[k] for k in ("filename", "size", "sha256")},
            f"input size/hash mismatch: {key}")


def _lame_sourceforge_identity(url: str) -> tuple[str, frozenset[str]]:
    """Validate SourceForge structure while keeping routing values opaque."""
    require(len(url.encode("utf-8")) <= LAME_MAX_URL_BYTES, "unsafe LAME source URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ProjectFFmpegError("unsafe LAME source URL") from exc
    require(parsed.scheme == "https" and not parsed.username and not parsed.password
            and not parsed.fragment and port in (None, 443), "unsafe LAME source URL")
    host = (parsed.hostname or "").lower()
    if host == "sourceforge.net" and parsed.path == LAME_ROUTING_PATH:
        host_class = "routing"
    elif host == "downloads.sourceforge.net" and parsed.path == LAME_PROJECT_PATH:
        host_class = "gateway"
    elif LAME_MIRROR_HOST.fullmatch(host) and parsed.path == LAME_PROJECT_PATH:
        host_class = "mirror"
    else:
        raise ProjectFFmpegError("unexpected LAME source identity")
    require(re.search(r"%(?![0-9A-Fa-f]{2})", parsed.query) is None,
            "malformed LAME routing metadata")
    require(len(parsed.query.encode("utf-8")) <= LAME_MAX_QUERY_BYTES,
            "overlong LAME routing metadata")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict") if parsed.query else []
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectFFmpegError("malformed LAME routing metadata") from exc
    keys = [key for key, _value in pairs]
    require(all(keys) and len(keys) == len(set(keys)), "malformed LAME routing metadata")
    values = dict(pairs)
    keyset = frozenset(keys)
    if host_class == "routing":
        require(not keyset, "unexpected LAME routing metadata")
    elif host_class == "gateway":
        require(keyset in (frozenset(), frozenset({"use_mirror"}),
                           frozenset({"r", "ts", "use_mirror"})),
                "unexpected LAME routing metadata")
        if keyset:
            mirror = values["use_mirror"]
            require(bool(mirror) and len(mirror) <= 64
                    and re.fullmatch(r"[A-Za-z0-9._-]+", mirror) is not None,
                    "invalid LAME gateway routing metadata")
        if "ts" in values:
            require(bool(values["ts"]), "invalid LAME gateway routing metadata")
    else:
        require(keyset in (frozenset(), frozenset({"viasf"}),
                           frozenset({"e", "fid", "st", "viasf"})),
                "unexpected LAME routing metadata")
        require(not keyset or values["viasf"] == "1",
                "invalid LAME mirror routing metadata")
        if len(keyset) == 4:
            require(all(values[name] for name in ("e", "fid", "st")),
                    "invalid LAME mirror routing metadata")
    return host_class, keyset


def validate_redirect(key: str, original: str, target: str) -> None:
    pin = INPUTS[key]
    if key == "lame":
        original_class, _original_keys = _lame_sourceforge_identity(original)
        target_class, _target_keys = _lame_sourceforge_identity(target)
        allowed = {
            "routing": {"gateway", "mirror"},
            "gateway": {"mirror"},
            "mirror": {"routing", "gateway"},
        }
        require(target_class in allowed[original_class], "unexpected LAME redirect transition")
        return
    require(original == pin["url"], "unexpected source URL/input")
    parsed = urlsplit(target)
    require(parsed.scheme == "https" and not parsed.username and not parsed.password
            and not parsed.fragment and parsed.port in (None, 443), "unsafe source redirect")
    require(parsed.hostname in pin["redirect_hosts"], "unexpected source redirect host")
    if key == "ffmpeg":
        require(parsed.path == f"/FFmpeg/FFmpeg/tar.gz/{FFMPEG_COMMIT}" and not parsed.query,
                "unexpected FFmpeg source redirect identity")
    elif key == "w64devkit":
        require(parsed.path.startswith("/github-production-release-asset/")
                or parsed.path.startswith("/github-production-release-asset-2e65be/"),
                "unexpected toolchain asset redirect path")


def parse_curl_write_out(stdout: bytes, key: str, hop: int, retry: int) -> tuple[str, str]:
    """Parse only curl's ASCII write-out fields; never surface raw response text."""
    try:
        raw = stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProjectFFmpegError(
            f"malformed HTTP response: {key} hop={hop} retry={retry}"
        ) from exc
    status_line, separator, redirect_part = raw.partition("\n")
    status = status_line.rstrip("\r")
    redirect = redirect_part.strip("\r\n")
    require(bool(separator) and re.fullmatch(r"[0-9]{3}", status) is not None
            and "\r" not in redirect and "\n" not in redirect,
            f"malformed HTTP response: {key} hop={hop} retry={retry}")
    return status, redirect


def remove_partial(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def acquire_input(key: str, root: Path) -> Path:
    """No redirect is followed before validation; all bytes stay quarantined until pinned."""
    pin = INPUTS[key]
    root.mkdir(parents=True, exist_ok=True)
    path = root / pin["filename"]
    if path.exists():
        verify_input(path, key)
        return path
    partial = root / (pin["filename"] + ".partial")
    url = pin["url"]
    if key == "lame":
        require(url == LAME_SOURCE_URL, "unexpected LAME source URL/input")
        initial_class, initial_keys = _lame_sourceforge_identity(url)
        require(initial_class == "routing" and not initial_keys, "unexpected LAME source URL/input")
        initial = urlsplit(url)
        seen = {(initial.scheme, initial.hostname, initial.path, initial_keys)}
        max_redirects = LAME_MAX_REDIRECTS
    else:
        seen = set()
        max_redirects = 1
    for hop_index in range(max_redirects + 1):
        hop = hop_index + 1
        for retry in range(len(HTTP_RETRY_DELAYS) + 1):
            remove_partial(partial)
            result = subprocess.run([
                "curl.exe", "--silent", "--show-error", "--proto", "=https",
                "--max-time", "600", "--max-filesize", str(pin["size"]),
                "--output", str(partial), "--write-out", "%{http_code}\n%{redirect_url}", url,
            ], capture_output=True, timeout=610,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # Never expose redirect query strings, HTTP headers, or raw curl diagnostics.
            if result.returncode != 0:
                remove_partial(partial)
                raise ProjectFFmpegError(f"bounded HTTPS acquisition failed: {key}")
            try:
                status, redirect = parse_curl_write_out(result.stdout, key, hop, retry)
            except ProjectFFmpegError:
                remove_partial(partial)
                raise
            if status == "200":
                if redirect or not partial.is_file() or partial.is_symlink():
                    remove_partial(partial)
                    raise ProjectFFmpegError(
                        f"unexpected HTTP response: {key} status={status} hop={hop} retry={retry}"
                    )
                valid = (partial.stat().st_size == pin["size"]
                         and hashlib.sha256(partial.read_bytes()).hexdigest() == pin["sha256"])
                if not valid:
                    remove_partial(partial)
                    raise ProjectFFmpegError(f"download size/hash mismatch: {key}")
                partial.rename(path)
                verify_input(path, key)
                return path
            remove_partial(partial)
            if status in TRANSIENT_HTTP_STATUSES:
                if retry < len(HTTP_RETRY_DELAYS):
                    time.sleep(HTTP_RETRY_DELAYS[retry])
                    continue
                raise ProjectFFmpegError(
                    f"unexpected HTTP response: {key} status={status} hop={hop} retry={retry}"
                )
            if status not in REDIRECT_HTTP_STATUSES or not redirect:
                raise ProjectFFmpegError(
                    f"unexpected HTTP response: {key} status={status} hop={hop} retry={retry}"
                )
            if hop_index >= max_redirects:
                remove_partial(partial)
                if key == "lame":
                    raise ProjectFFmpegError(f"redirect limit exceeded: {key}")
                raise ProjectFFmpegError(
                    f"unexpected HTTP response: {key} status={status} hop={hop} retry={retry}"
                )
            validate_redirect(key, url, redirect)
            if key == "lame":
                _target_class, target_keys = _lame_sourceforge_identity(redirect)
                target = urlsplit(redirect)
                identity = (target.scheme, target.hostname, target.path, target_keys)
                require(identity not in seen, "LAME redirect loop detected")
                seen.add(identity)
            url = redirect
            break
    raise ProjectFFmpegError(f"redirect limit exceeded: {key}")


def safe_member(name: str) -> None:
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name
            and ":" not in name, "unsafe source archive member")


def extract_tar(source: Path, target: Path, key: str, prefix: str) -> None:
    verify_input(source, key)
    require(not target.exists(), "refusing to overlay an existing source tree")
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            safe_member(member.name)
            require(member.name == prefix or member.name.startswith(prefix + "/"), "source root mismatch")
            require(member.isdir() or member.isfile(), "source archive link or special entry")
            require(member.name.casefold() not in seen, "source archive case collision")
            seen.add(member.name.casefold())
        target.mkdir(parents=True)
        for member in members:
            relative = PurePosixPath(member.name).relative_to(prefix)
            destination = target.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                require(stream is not None, "unreadable source member")
                destination.write_bytes(stream.read())


def validate_configuration(configuration: list[str]) -> None:
    require(configuration == CONFIGURE, "FFmpeg configure drift")
    require([x.removeprefix("--enable-") for x in configuration
             if x.startswith("--enable-lib") or x == "--enable-schannel"] == EXTERNAL_LIBRARIES,
            "unexpected external library")
    require(not any(x in configuration for x in ("--enable-gpl", "--enable-nonfree", "--enable-version3", "--enable-libx264", "--enable-libx265")),
            "unexpected GPL/nonfree/video encoder configuration")


def validate_components(kind: str, output: str) -> None:
    words = set(re.findall(r"[a-zA-Z0-9_]+", output))
    require(REQUIRED_COMPONENTS[kind] <= words, f"required {kind} capability missing")


def validate_dlls(names: list[str]) -> None:
    require(bool(names) and len(names) == len(set(names)), "invalid DLL inspection")
    require(set(n.casefold() for n in names) <= SYSTEM_DLLS, "unexpected runtime DLL dependency")


def run(argv: list[str], *, cwd: Path, env: dict, log: Path, timeout: int = 7200) -> str:
    with log.open("wb") as stream:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(result.returncode == 0, f"build command failed; inspect {log.name}")
    return log.read_text(encoding="utf-8", errors="replace")

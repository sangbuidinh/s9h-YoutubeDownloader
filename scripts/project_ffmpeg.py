"""Pinned source/build contract for the project FFmpeg runtime (not authorization)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from urllib.parse import urlsplit
import zipfile

FFMPEG_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"
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
        "url": "https://zenlayer.dl.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz",
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


def validate_redirect(key: str, original: str, target: str) -> None:
    pin = INPUTS[key]
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
    for hop in range(2):
        result = subprocess.run([
            "curl.exe", "--silent", "--show-error", "--proto", "=https",
            "--max-time", "600", "--max-filesize", str(pin["size"]),
            "--output", str(partial), "--write-out", "%{http_code}\n%{redirect_url}", url,
        ], capture_output=True, timeout=610, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # Never expose redirect query strings, HTTP headers, or raw curl diagnostics.
        require(result.returncode == 0, f"bounded HTTPS acquisition failed: {key}")
        lines = result.stdout.decode("ascii").split("\n", 1)
        if lines[0] == "200":
            require(partial.stat().st_size == pin["size"]
                    and hashlib.sha256(partial.read_bytes()).hexdigest() == pin["sha256"],
                    f"download size/hash mismatch: {key}")
            partial.rename(path)
            verify_input(path, key)
            return path
        require(hop == 0 and lines[0] in {"301", "302", "303", "307", "308"}
                and len(lines) == 2, f"unexpected HTTP response: {key}")
        validate_redirect(key, pin["url"], lines[1])
        url = lines[1]
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

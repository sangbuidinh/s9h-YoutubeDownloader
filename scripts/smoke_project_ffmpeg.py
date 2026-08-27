"""Offline fail-closed tests for the project-controlled FFmpeg build boundary."""
import copy
import hashlib
from pathlib import Path
import tempfile
import zipfile
from unittest import mock

import project_ffmpeg as runtime
import source_compliance
import prepare_source_kit
from verify_project_ffmpeg import validate_probe_output


def reject(action, label):
    try:
        action()
    except (runtime.ProjectFFmpegError, source_compliance.SourceComplianceError):
        return
    raise AssertionError(f"negative accepted: {label}")


def main():
    count = 0
    runtime.validate_configuration(runtime.CONFIGURE)
    for flag in ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265", "--enable-libopus"):
        reject(lambda flag=flag: runtime.validate_configuration([*runtime.CONFIGURE, flag]), flag)
        count += 1
    reject(lambda: runtime.validate_configuration([v for v in runtime.CONFIGURE if v != "--enable-libmp3lame"]), "MP3 encoder omitted")
    count += 1
    for kind, needed in runtime.REQUIRED_COMPONENTS.items():
        runtime.validate_components(kind, "\n".join(sorted(needed)))
        for component in needed:
            reject(lambda kind=kind, component=component: runtime.validate_components(kind, "\n".join(needed - {component})), f"{kind}/{component} missing")
            count += 1
    runtime.validate_dlls(["KERNEL32.dll", "msvcrt.dll"])
    reject(lambda: runtime.validate_dlls(["KERNEL32.dll", "libwinpthread-1.dll"]), "unexpected DLL")
    reject(lambda: runtime.validate_dlls([]), "missing dependency inspection")
    count += 2
    ffmpeg = runtime.INPUTS["ffmpeg"]
    runtime.validate_redirect("ffmpeg", ffmpeg["url"], f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{runtime.FFMPEG_COMMIT}")
    for target in (
        "http://codeload.github.com/FFmpeg/FFmpeg/tar.gz/" + runtime.FFMPEG_COMMIT,
        "https://example.invalid/ffmpeg.tar.gz",
        "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/master",
        f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{runtime.FFMPEG_COMMIT}?ref=latest",
    ):
        reject(lambda target=target: runtime.validate_redirect("ffmpeg", ffmpeg["url"], target), "source redirect/input drift")
        count += 1
    reject(lambda: runtime.validate_redirect("ffmpeg", "https://example.invalid/source", ffmpeg["url"]), "arbitrary initial input")
    reject(lambda: runtime.validate_redirect("lame", runtime.INPUTS["lame"]["url"], "https://example.invalid/lame.tar.gz"), "LAME redirect")
    count += 2
    with tempfile.TemporaryDirectory(prefix="project-ffmpeg-pin-") as temp:
        pin = copy.deepcopy(ffmpeg)
        pin.update(filename="synthetic-source.tar.gz", size=4, sha256=hashlib.sha256(b"test").hexdigest())
        source = Path(temp) / pin["filename"]
        with mock.patch.dict(runtime.INPUTS, {"synthetic": pin}):
            source.write_bytes(b"test")
            runtime.verify_input(source, "synthetic")
            source.write_bytes(b"best")
            reject(lambda: runtime.verify_input(source, "synthetic"), "same-size source hash mismatch")
            source.write_bytes(b"longer")
            reject(lambda: runtime.verify_input(source, "synthetic"), "source size mismatch")
        count += 2
        reject(lambda: runtime.verify_input(source, "unknown"), "unknown source")
        count += 1
    for name in ("../outside", "/absolute", "C:/outside", "root\\outside"):
        reject(lambda name=name: runtime.safe_member(name), "archive traversal")
        count += 1
    for output in ('{}', '{"streams": [], "format": {"duration": "2"}}',
                   '{"streams": [{}], "format": {"duration": "2"}}',
                   '{"streams": [{"codec_name": "mp3", "codec_type": "audio"}], "format": {}}'):
        reject(lambda output=output: validate_probe_output(output), "missing ffprobe capability")
        count += 1
    with tempfile.TemporaryDirectory(prefix="project-source-determinism-") as temp:
        path = Path(temp) / "source.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("NOTICE.txt", (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = source_compliance.FIXED_FILE_MODE
            archive.writestr(info, b"synthetic fixture", compress_type=zipfile.ZIP_DEFLATED)
        reject(lambda: source_compliance._read_deterministic_zip(path, "synthetic"), "source-kit non-determinism")
        count += 1
        root = Path(__file__).resolve().parents[1]
        owner = source_compliance.load_owner(root / source_compliance.OWNER_PATH)
        if "runtime_build" in owner["kits"][1]:
            candidate = copy.deepcopy(owner)
            candidate["kits"][1]["runtime_build"]["binaries"][0]["sha256"] = "0" * 64
            (Path(temp) / "ffmpeg.exe").write_bytes(b"MZ unreviewed runtime")
            (Path(temp) / "ffprobe.exe").write_bytes(b"MZ unreviewed probe")
            reject(lambda: prepare_source_kit.verify_project_runtime(candidate, Path(temp), root), "runtime binary/source-owner mismatch")
            count += 1
            candidate = copy.deepcopy(owner)
            asset = candidate["kits"][1]["source_asset"]
            path = Path(temp) / asset["filename"]
            data = b"synthetic same-size unreviewed source kit"
            path.write_bytes(data)
            asset["size"] = len(data)
            reject(lambda: source_compliance.verify_source_asset(candidate, "ffmpeg", path), "source-kit SHA mismatch")
            count += 1
    print(f"Project FFmpeg source/configure/capability boundary passed: {count} negatives; no runtime executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

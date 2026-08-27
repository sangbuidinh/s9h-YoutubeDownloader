"""Local synthetic capability comparison; never resolves URLs or reads cookies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import project_ffmpeg as contract

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import downloader
from core import ytdlp_commands
from core.download_contracts import DownloadOptions
from core.download_contracts import DownloadCancelled
from core.download_process import DownloadController
from core.ffmpeg_tools import run_ffmpeg_command, FFmpegExecutionError

OLD_FFMPEG_SHA = "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e"
OLD_FFPROBE_SHA = "b49ccc7c6547b141ad5a2f6ec69cc04323d7133d7704d70b331b904c63eecb07"


def execute(argv, *, expected=0):
    result = subprocess.run([str(v) for v in argv], capture_output=True, timeout=60,
                            encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    contract.require(result.returncode == expected, f"synthetic command failed: {Path(argv[0]).name}; exit={result.returncode}")
    return result.stdout, result.stderr


def metadata(probe, path):
    out, _ = execute([probe, "-hide_banner", "-show_format", "-show_streams", "-print_format", "json", path])
    value = json.loads(out)
    contract.require(isinstance(value.get("streams"), list) and value.get("format"), "ffprobe JSON metadata missing")
    return value


def validate_probe_output(output):
    value = json.loads(output)
    contract.require(isinstance(value, dict) and isinstance(value.get("streams"), list)
                     and bool(value["streams"]) and isinstance(value.get("format"), dict)
                     and "duration" in value["format"], "ffprobe capability/metadata missing")
    contract.require(all(isinstance(stream, dict) and stream.get("codec_name")
                         and stream.get("codec_type") for stream in value["streams"]), "ffprobe stream codec metadata missing")


def compare(runtime_root: Path, build_manifest: Path, reference: Path, fixtures: Path):
    manifest = json.loads(build_manifest.read_text(encoding="utf-8"))
    contract.require(manifest["inputs"] == contract.INPUTS, "build source identity drift")
    contract.validate_configuration(manifest["configure"])
    for binary in manifest["binaries"]:
        actual = contract.file_identity(runtime_root / binary["filename"])
        contract.require(actual == {k: binary[k] for k in actual}, "runtime binary/source manifest mismatch")
    for name, sha in (("ffmpeg.exe", OLD_FFMPEG_SHA), ("ffprobe.exe", OLD_FFPROBE_SHA)):
        contract.require(hashlib.sha256((reference / name).read_bytes()).hexdigest() == sha, "historical reference binary hash mismatch")
    contract.require(not fixtures.exists(), "synthetic fixture directory must be new")
    fixtures.mkdir(parents=True)
    generator = reference / "ffmpeg.exe"
    # H264 encoding exists ONLY in this synthetic test generator, using the old
    # verified reference runtime. It is not a production runtime requirement.
    inputs = {
        "video.mp4": ["-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"],
        "audio.m4a": ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"],
        "opus.webm": ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libopus"],
        "vorbis.webm": ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libvorbis"],
    }
    for name, args in inputs.items():
        execute([generator, "-y", *args, fixtures / name])
    summary = {"fixture_kind": "locally generated solid color and sine; no user media", "runtime_results": {}}
    for label, folder in (("old-verified-reference", reference), ("project-controlled", runtime_root)):
        output = fixtures / label
        output.mkdir()
        ffmpeg, probe = folder / "ffmpeg.exe", folder / "ffprobe.exe"
        for kind in contract.REQUIRED_COMPONENTS:
            stdout, stderr = execute([ffmpeg, "-hide_banner", "-" + kind])
            contract.validate_components(kind, stdout + stderr)
        for name in inputs:
            value = metadata(probe, fixtures / name)
            validate_probe_output(json.dumps(value))
        progress = []
        with mock.patch.object(downloader, "runtime_file", lambda name: folder / name):
            merged = output / "merged.mp4"
            argv = downloader._build_fast_hybrid_merge_command(fixtures / "video.mp4", fixtures / "audio.m4a", merged)
            run_ffmpeg_command(argv, operation="merge_video", progress_duration_seconds=2,
                               progress_emitter=lambda *args, **kwargs: progress.append(kwargs))
            contract.require(progress and progress[-1]["percent"] == "100%", "production progress parser did not reach completion")
            downloader._validate_premiere_safe_mp4(merged, delete_invalid=False)
            contract.require(downloader._probe_media_duration_seconds(merged) > 0, "production duration probe failed")
            remux = output / "combined-remux.mp4"
            execute([ffmpeg, "-y", "-i", merged, "-map", "0", "-c", "copy", "-movflags", "+faststart", remux])
            downloader._validate_premiere_safe_mp4(remux, delete_invalid=False)
            for source in ("audio.m4a", "opus.webm", "vorbis.webm"):
                target = output / (source + ".mp3")
                # Exact pinned yt-dlp MP3 encoding/quality options, including its
                # shared faststart option; real yt-dlp postprocessors are also
                # exercised separately in the integration matrix.
                execute([ffmpeg, "-y", "-loglevel", "repeat+info", "-i", fixtures / source,
                         "-vn", "-acodec", "libmp3lame", "-q:a", "0.0", "-movflags", "+faststart", target])
                parsed = metadata(probe, target)
                contract.require(parsed["streams"][0]["codec_name"] == "mp3", "MP3 encoding contract failed")
                contract.require(1.8 < float(parsed["format"]["duration"]) < 2.3, "MP3 duration regression")
            audio_temp = output / "audio-temp"
            audio_temp.mkdir()
            downloader._extract_mp3_from_video(merged, audio_temp, output / "owned-audio.mp3")
            contract.require(metadata(probe, output / "owned-audio.mp3")["streams"][0]["codec_name"] == "mp3", "owned MP4 audio extraction failed")
        _, diagnostic = execute([ffmpeg, "-hide_banner", "-i", merged], expected=1)
        contract.require(downloader._parse_premiere_safe_probe_output(diagnostic)[0], "ffmpeg fallback probe parser failed")
        try:
            run_ffmpeg_command([str(ffmpeg), "-i", str(output / "not-present.mp4")], operation="invalid_input")
        except FFmpegExecutionError as exc:
            contract.require(exc.exit_code != 0 and bool(exc.output_lines), "missing nonzero/error diagnostic")
        else:
            raise contract.ProjectFFmpegError("invalid input was accepted")
        controller = DownloadController()
        timer = threading.Timer(0.7, controller.request_cancel)
        timer.start()
        try:
            run_ffmpeg_command([str(ffmpeg), "-y", "-re", "-f", "lavfi", "-i", "sine=duration=30",
                                "-c:a", "libmp3lame", str(output / "cancelled.mp3")],
                               operation="cancel_test", cancel_controller=controller)
        except DownloadCancelled:
            pass
        else:
            raise contract.ProjectFFmpegError("production cancellation did not stop owned process")
        finally:
            timer.cancel()
        summary["runtime_results"][label] = {"probe": "PASS", "copy_merge": "PASS", "combined_remux": "PASS",
            "aac_opus_vorbis_to_mp3_quality_zero": "PASS", "owned_audio_extraction": "PASS",
            "progress": "PASS", "error": "PASS", "cancel": "PASS", "metadata": "PASS"}
    return summary


def exercise_pinned_ytdlp(runtime: Path, fixtures: Path, ytdlp: Path, aria2: Path) -> dict:
    for path, expected in ((ytdlp, "652e154bce7170070d0f26415c9a3c35c121f5a7903cb8cde6d31c4577517fb9"),
                           (aria2, "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2")):
        contract.require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "postprocessor test runtime pin mismatch")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(fixtures)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    resolver = lambda name: {"yt-dlp.exe": ytdlp, "aria2c.exe": aria2}.get(name, runtime / name)
    options = DownloadOptions(str(fixtures), "synthetic", "synthetic", cookies_enabled=False)
    result = {}
    try:
        for fast in (False, True):
            label = "fast" if fast else "stable"
            for name in ("audio.m4a", "opus.webm", "vorbis.webm"):
                folder = fixtures / ("ytdlp-" + label + "-" + name)
                folder.mkdir()
                if fast:
                    validation = ytdlp_commands._Aria2RuntimeValidation(True, True, aria2)
                    argv = ytdlp_commands._build_fast_audio_ytdlp_command("synthetic", folder, options, validation, runtime_file_resolver=resolver)
                else:
                    argv = ytdlp_commands._build_stable_audio_ytdlp_command("synthetic", folder, options, runtime_file_resolver=resolver)
                # Only fixture location and process configuration isolation differ
                # from production argv. Never read configured cookies or aria2rc.
                argv[-1] = base + "/" + name
                argv[1:1] = ["--ignore-config", "--no-cache-dir", "--downloader-args", "aria2c:--no-conf=true"]
                execute(argv)
                outputs = list(folder.glob("*.mp3"))
                contract.require(len(outputs) == 1, "pinned yt-dlp MP3 postprocess missing")
                contract.require(metadata(runtime / "ffprobe.exe", outputs[0])["streams"][0]["codec_name"] == "mp3", "pinned yt-dlp MP3 codec mismatch")
                result[label + "-" + name] = "PASS"
        folder = fixtures / "ytdlp-stable-video"
        folder.mkdir()
        info = {"id": "synthetic", "title": "Synthetic color and sine", "extractor": "generic", "extractor_key": "Generic",
                "webpage_url": base + "/video.mp4", "duration": 2,
                "formats": [
                    {"format_id": "video", "url": base + "/video.mp4", "ext": "mp4", "protocol": "http", "vcodec": "avc1.64000a", "acodec": "none", "height": 90, "width": 160},
                    {"format_id": "audio", "url": base + "/audio.m4a", "ext": "m4a", "protocol": "http", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
                ]}
        snapshot = folder / "synthetic.info.json"
        snapshot.write_bytes(contract.canonical(info))
        argv = ytdlp_commands._build_stable_video_ytdlp_command("synthetic", folder, options, runtime_file_resolver=resolver)
        argv[-1:] = ["--load-info-json", str(snapshot)]
        argv[1:1] = ["--ignore-config", "--no-cache-dir"]
        execute(argv)
        outputs = list(folder.glob("*.mp4"))
        contract.require(len(outputs) == 1, "pinned yt-dlp Stable merge missing")
        with mock.patch.object(downloader, "runtime_file", lambda name: runtime / name):
            downloader._validate_premiere_safe_mp4(outputs[0], delete_invalid=False)
        result["stable-video-pinned-ytdlp-merger"] = "PASS"
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--fixtures-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--yt-dlp", type=Path)
    parser.add_argument("--aria2", type=Path)
    args = parser.parse_args()
    args.result.parent.mkdir(parents=True, exist_ok=True)
    contract.require(not args.result.exists(), "result path must be new")
    result = compare(args.runtime_root, args.build_manifest, args.reference_root, args.fixtures_root)
    contract.require(bool(args.yt_dlp) == bool(args.aria2), "both pinned postprocessor test tools are required together")
    if args.yt_dlp:
        result["pinned_ytdlp_integration"] = exercise_pinned_ytdlp(args.runtime_root, args.fixtures_root, args.yt_dlp, args.aria2)
    args.result.write_bytes(contract.canonical(result))
    print("Synthetic old/new FFmpeg capability and production parser/cancel comparison: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

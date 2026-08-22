from __future__ import annotations

import argparse
import dataclasses
import os
import queue
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import core.downloader as downloader
from core.progress_status import ProgressEvent, format_progress_event_lines, put_latest_progress_event
from ui.main_window import YouTubeDownloaderWindow

import diagnose_fast_exact_binaries as exact_probe


POLL_SECONDS = 0.300
MAX_RAW_CHARS = 2000


def _log_path() -> Path:
    root = Path(tempfile.gettempdir()) / "s9h-ytdl-diag"
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        root.glob("fast-app-progress-pipeline-*.jsonl"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for stale in existing[4:]:
        try:
            stale.unlink()
        except OSError:
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return root / f"fast-app-progress-pipeline-{stamp}-pid{os.getpid()}.jsonl"


def _event_dict(event: ProgressEvent) -> dict[str, object]:
    return dataclasses.asdict(event)


def _clean_raw(raw: object) -> str:
    text = str(raw or "").replace("\x00", "")
    if len(text) > MAX_RAW_CHARS:
        text = text[:MAX_RAW_CHARS] + "<truncated>"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace the real app Fast progress reader/parser/queue/display bridge using exact bundled binaries."
    )
    parser.add_argument("--root", required=True, type=Path, help="SHA-tagged exact-binary diagnostic bundle root")
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = exact_probe._load_build_manifest(root)
    source_sha = str(manifest["source_sha"])
    yt_dlp = root / "data" / "bin" / "yt-dlp.exe"
    aria2 = root / "data" / "bin" / "aria2c.exe"
    for required in (yt_dlp, aria2):
        if not required.is_file():
            raise FileNotFoundError(f"Missing exact bundled runtime binary: {required}")

    log_path = _log_path()
    temp_root = Path(tempfile.mkdtemp(prefix="s9h-fast-app-pipeline-"))
    server = exact_probe.ProbeServer(exact_probe.PROBE_BYTES, exact_probe.PROBE_RATE_BYTES_PER_SECOND)
    server_thread = threading.Thread(target=server.serve_forever, name="app-pipeline-probe-http", daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/probe.mp4"

    progress_queue: queue.Queue = queue.Queue(maxsize=1)
    callback_events: list[ProgressEvent] = []
    parser_matches: list[float] = []
    rendered_events: list[ProgressEvent] = []
    stop_poller = threading.Event()

    view = object.__new__(YouTubeDownloaderWindow)
    view._reset_progress_sticky(reset_order=True)

    previous_parse = downloader.parse_aria2_progress
    previous_context = None
    poller_thread: threading.Thread | None = None
    exit_code = 1

    print(f"Diagnostic source SHA: {source_sha}")
    print(f"JSONL: {log_path}")

    try:
        with exact_probe.JsonlWriter(log_path) as writer:
            def record(event: str, **fields: object) -> None:
                writer.write(event, source_sha=source_sha, **fields)

            record(
                "app_pipeline_start",
                pid=os.getpid(),
                poll_seconds=POLL_SECONDS,
                yt_dlp_sha256=exact_probe._sha256(yt_dlp),
                aria2_sha256=exact_probe._sha256(aria2),
            )

            def traced_parse(raw_line: object):
                parsed = previous_parse(raw_line)
                if parsed is not None and parsed.percent is not None:
                    parser_matches.append(float(parsed.percent))
                record(
                    "app_parser_call",
                    raw=_clean_raw(raw_line),
                    matched=parsed is not None,
                    percent=parsed.percent if parsed is not None else None,
                    speed=parsed.speed_text if parsed is not None else None,
                )
                return parsed

            def progress_callback(event: ProgressEvent) -> None:
                callback_events.append(event)
                record("app_progress_callback", progress=_event_dict(event))
                put_latest_progress_event(progress_queue, event)

            def poll_progress() -> None:
                while True:
                    latest = None
                    try:
                        while True:
                            latest = progress_queue.get_nowait()
                    except queue.Empty:
                        pass
                    if latest is not None:
                        merged = view._merge_progress_event_for_display(latest)
                        rendered_events.append(merged)
                        current_line, detail_line = format_progress_event_lines(merged)
                        record(
                            "ui_poll_render",
                            latest=_event_dict(latest),
                            merged=_event_dict(merged),
                            current_line=current_line,
                            detail_line=detail_line,
                        )
                    if stop_poller.wait(POLL_SECONDS):
                        latest = None
                        try:
                            while True:
                                latest = progress_queue.get_nowait()
                        except queue.Empty:
                            pass
                        if latest is not None:
                            merged = view._merge_progress_event_for_display(latest)
                            rendered_events.append(merged)
                            current_line, detail_line = format_progress_event_lines(merged)
                            record(
                                "ui_poll_render_final",
                                latest=_event_dict(latest),
                                merged=_event_dict(merged),
                                current_line=current_line,
                                detail_line=detail_line,
                            )
                        return

            downloader.parse_aria2_progress = traced_parse
            video = SimpleNamespace(title="Exact app progress probe", sanitized_filename_base="001 Exact app progress probe")
            previous_context = downloader._set_progress_context(progress_callback, video, 1, 1, "Video")
            poller_thread = threading.Thread(target=poll_progress, name="app-progress-poll", daemon=True)
            poller_thread.start()

            output_dir = temp_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            command = [
                str(yt_dlp),
                "--no-playlist",
                "--newline",
                "--no-overwrites",
                "--retries",
                "30",
                "--fragment-retries",
                "30",
                "--file-access-retries",
                "10",
                "--socket-timeout",
                "60",
                "--http-chunk-size",
                "1M",
                "--force-generic-extractor",
                "--downloader",
                str(aria2),
                "--downloader-args",
                "aria2c:-x 16 -s 16 -j 16 -k 1M",
                "-o",
                str(output_dir / "probe-app.%(ext)s"),
                url,
            ]

            started = time.monotonic()
            downloader._run_ytdlp(command)
            elapsed = time.monotonic() - started
            time.sleep(POLL_SECONDS * 1.2)

            callback_percent = [event.percent for event in callback_events if event.percent]
            rendered_percent = [event.percent for event in rendered_events if event.percent]
            passed = bool(parser_matches and callback_percent and rendered_percent)
            record(
                "app_pipeline_result",
                passed=passed,
                elapsed_seconds=round(elapsed, 3),
                parser_progress_count=len(parser_matches),
                parser_percents=parser_matches,
                callback_progress_count=len(callback_percent),
                callback_percents=callback_percent,
                rendered_progress_count=len(rendered_percent),
                rendered_percents=rendered_percent,
            )
            exit_code = 0 if passed else 2
    except Exception as exc:
        try:
            with exact_probe.JsonlWriter(log_path) as writer:
                writer.write(
                    "app_pipeline_exception",
                    source_sha=source_sha,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
        except Exception:
            pass
        print(f"App pipeline probe failed: {type(exc).__name__}: {exc}")
        exit_code = 1
    finally:
        stop_poller.set()
        if poller_thread is not None:
            poller_thread.join(timeout=3)
        if previous_context is not None:
            downloader._restore_progress_context(previous_context)
        downloader.parse_aria2_progress = previous_parse
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)

    if exit_code == 0:
        print("PASS: app reader/parser/emitter/latest-queue/display-merge pipeline produced live Fast progress.")
    elif exit_code == 2:
        print("FAIL: app progress disappeared between exact stdout and the app display bridge; inspect JSONL.")
    print(f"JSONL: {log_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

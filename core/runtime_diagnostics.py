from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import progress_status
from core.runtime_paths import app_root


MARKER_NAME = "DIAGNOSTIC_BUILD.json"
DIAG_DIR_NAME = "s9h-ytdl-diag"
MAX_SESSION_FILES = 8
MAX_TEXT_CHARS = 1200


class RuntimeDiagnosticSession:
    def __init__(self, source_sha: str, purpose: str, marker: dict[str, Any]):
        self.source_sha = source_sha
        self.purpose = purpose
        self.marker = marker
        self.pid = os.getpid()
        self.started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self._sequence = 0
        self._attempt_local = threading.local()

        root = Path(tempfile.gettempdir()) / DIAG_DIR_NAME
        root.mkdir(parents=True, exist_ok=True)
        self._prune(root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = root / f"runtime-{source_sha[:12]}-{stamp}-pid{self.pid}.jsonl"
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self.write(
            "session_start",
            purpose=purpose,
            marker_schema=marker.get("schema_version"),
            workflow_run_id=marker.get("workflow_run_id"),
            workflow_run_attempt=marker.get("workflow_run_attempt"),
            app_root_fingerprint=_path_fingerprint(app_root()),
        )

    @staticmethod
    def _prune(root: Path) -> None:
        try:
            files = sorted(
                root.glob("runtime-*.jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for stale in files[MAX_SESSION_FILES - 1 :]:
            try:
                stale.unlink()
            except OSError:
                pass

    def write(self, event: str, **fields: Any) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "ts_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_seconds": round(time.monotonic() - self.started_monotonic, 6),
                "sequence": self._sequence,
                "event": event,
                "source_sha": self.source_sha,
                "pid": self.pid,
                **fields,
            }
            line = json.dumps(_json_safe(record), ensure_ascii=False, separators=(",", ":"))
            self._handle.write(line + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        with self._lock:
            if self._handle.closed:
                return
            try:
                self.write("session_end")
            except Exception:
                pass
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()

    def begin_fast_attempt(self, command: list[str] | tuple[str, ...]) -> None:
        state = {
            "parser_calls": 0,
            "parser_matches": 0,
            "emit_calls": 0,
            "queue_events": 0,
            "ui_merges": 0,
            "first_parser_match_s": None,
            "started": time.monotonic(),
        }
        self._attempt_local.fast = state
        self.write(
            "fast_run_start",
            downloader=_command_option_value(command, "--downloader"),
            has_downloader_args=bool(_command_option_value(command, "--downloader-args")),
        )

    def current_fast_attempt(self) -> dict[str, Any] | None:
        value = getattr(self._attempt_local, "fast", None)
        return value if isinstance(value, dict) else None

    def end_fast_attempt(self, *, outcome: str, error_type: str | None = None) -> None:
        state = self.current_fast_attempt()
        if state is None:
            return
        elapsed = time.monotonic() - float(state["started"])
        self.write(
            "fast_run_end",
            outcome=outcome,
            error_type=error_type,
            elapsed_seconds=round(elapsed, 6),
            parser_calls=state["parser_calls"],
            parser_matches=state["parser_matches"],
            emit_calls=state["emit_calls"],
            queue_events=state["queue_events"],
            ui_merges=state["ui_merges"],
            first_parser_match_seconds=state["first_parser_match_s"],
        )
        self._attempt_local.fast = None


def load_diagnostic_session() -> RuntimeDiagnosticSession | None:
    marker_path = app_root() / MARKER_NAME
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(marker, dict):
        return None
    source_sha = str(marker.get("source_sha", "")).strip().lower()
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        return None
    purpose = str(marker.get("purpose", "runtime-bug-diagnostic") or "runtime-bug-diagnostic")
    return RuntimeDiagnosticSession(source_sha, purpose, marker)


def install_downloader_diagnostics(downloader, session: RuntimeDiagnosticSession) -> None:
    if getattr(downloader, "_s9h_runtime_diagnostics_installed", False):
        return
    downloader._s9h_runtime_diagnostics_installed = True

    original_prepare = downloader._prepare_numbered_output_for_video
    original_promote = downloader._atomic_promote_with_retry
    original_run = downloader._run_ytdlp
    original_parse = downloader.parse_aria2_progress
    original_emit = downloader._emit_aria2_progress
    original_queue_put = progress_status.put_latest_progress_event

    def traced_prepare(video, options, selected_index: int):
        session.write(
            "filename_before_numbering",
            video_id=_bounded(getattr(video, "video_id", "")),
            canonical_title=_bounded(getattr(video, "title", "")),
            sanitized_before=_bounded(getattr(video, "sanitized_filename_base", "")),
            selected_index=selected_index,
            file_start_number=getattr(options, "file_start_number", None),
            channel_id=_bounded(getattr(options, "channel_id", "")),
            channel_name=_bounded(getattr(options, "channel_name", "")),
            base_folder_fingerprint=_path_fingerprint(getattr(options, "base_folder", "")),
        )
        try:
            assigned_number, stem, paths = original_prepare(video, options, selected_index)
        except Exception as exc:
            session.write(
                "filename_numbering_exception",
                video_id=_bounded(getattr(video, "video_id", "")),
                error_type=type(exc).__name__,
                message=_bounded(str(exc)),
            )
            raise
        session.write(
            "filename_after_numbering",
            video_id=_bounded(getattr(video, "video_id", "")),
            assigned_number=assigned_number,
            stem=_bounded(stem),
            sanitized_after=_bounded(getattr(video, "sanitized_filename_base", "")),
            channel_dir_fingerprint=_path_fingerprint(paths.channel_dir),
            video_basename=paths.video_path.name,
            thumb_basename=paths.thumb_path.name,
            audio_basename=paths.audio_path.name,
            video_target_fingerprint=_path_fingerprint(paths.video_path),
            thumb_target_fingerprint=_path_fingerprint(paths.thumb_path),
            audio_target_fingerprint=_path_fingerprint(paths.audio_path),
        )
        return assigned_number, stem, paths

    def traced_promote(source_path, final_path, log=None, replace_existing=False, cancel_controller=None):
        session.write(
            "promotion_before",
            source_basename=Path(source_path).name,
            final_basename=Path(final_path).name,
            final_target_fingerprint=_path_fingerprint(final_path),
            replace_existing=bool(replace_existing),
            source_size=_file_size(source_path),
            final_exists=Path(final_path).exists(),
            final_size=_file_size(final_path),
        )
        started = time.monotonic()
        try:
            result = original_promote(
                source_path,
                final_path,
                log,
                replace_existing=replace_existing,
                cancel_controller=cancel_controller,
            )
        except Exception as exc:
            session.write(
                "promotion_exception",
                source_basename=Path(source_path).name,
                final_basename=Path(final_path).name,
                final_target_fingerprint=_path_fingerprint(final_path),
                elapsed_seconds=round(time.monotonic() - started, 6),
                error_type=type(exc).__name__,
                message=_bounded(str(exc)),
            )
            raise
        session.write(
            "promotion_after",
            source_basename=Path(source_path).name,
            final_basename=Path(final_path).name,
            final_target_fingerprint=_path_fingerprint(final_path),
            elapsed_seconds=round(time.monotonic() - started, 6),
            source_exists=Path(source_path).exists(),
            final_exists=Path(final_path).exists(),
            final_size=_file_size(final_path),
        )
        return result

    def traced_parse(raw_line):
        state = session.current_fast_attempt()
        if state is not None:
            state["parser_calls"] += 1
        parsed = original_parse(raw_line)
        if parsed is not None:
            if state is not None:
                state["parser_matches"] += 1
                if state["first_parser_match_s"] is None:
                    state["first_parser_match_s"] = round(time.monotonic() - float(state["started"]), 6)
            session.write(
                "fast_parser_match",
                percent=parsed.percent,
                speed=parsed.speed_text,
                downloaded=parsed.downloaded_text,
                total=parsed.total_text,
                connections=parsed.connection_count,
                eta=parsed.eta_text,
                raw=_bounded(str(raw_line).replace("\x00", "")),
            )
        elif _looks_like_aria2_progress(raw_line):
            session.write("fast_parser_miss_candidate", raw=_bounded(str(raw_line).replace("\x00", "")))
        return parsed

    def traced_emit(progress):
        state = session.current_fast_attempt()
        if state is not None:
            state["emit_calls"] += 1
        session.write(
            "fast_emit",
            percent=getattr(progress, "percent", None),
            speed=getattr(progress, "speed_text", None),
        )
        return original_emit(progress)

    def traced_run(command, cancel_controller=None):
        uses_aria2 = False
        try:
            uses_aria2 = bool(downloader._command_uses_aria2(command))
        except Exception:
            pass
        if uses_aria2:
            session.begin_fast_attempt(command)
        try:
            result = original_run(command, cancel_controller)
        except Exception as exc:
            if uses_aria2:
                session.end_fast_attempt(outcome="exception", error_type=type(exc).__name__)
            raise
        if uses_aria2:
            session.end_fast_attempt(outcome="success")
        return result

    def traced_queue_put(progress_queue, event):
        state = session.current_fast_attempt()
        if state is not None and getattr(event, "source", None) == progress_status.TRANSFER_SOURCE_ARIA2:
            state["queue_events"] += 1
            session.write(
                "fast_queue_put",
                progress=_progress_event_payload(event),
                queue_size_before=_safe_qsize(progress_queue),
            )
        return original_queue_put(progress_queue, event)

    downloader._prepare_numbered_output_for_video = traced_prepare
    downloader._atomic_promote_with_retry = traced_promote
    downloader.parse_aria2_progress = traced_parse
    downloader._emit_aria2_progress = traced_emit
    downloader._run_ytdlp = traced_run
    progress_status.put_latest_progress_event = traced_queue_put

    session.write("downloader_diagnostics_installed")


def install_ui_diagnostics(main_window_module, session: RuntimeDiagnosticSession) -> None:
    window_class = main_window_module.YouTubeDownloaderWindow
    if getattr(window_class, "_s9h_runtime_diagnostics_installed", False):
        return
    window_class._s9h_runtime_diagnostics_installed = True

    original_merge = window_class._merge_progress_event_for_display
    original_finish = window_class._finish_download_ui

    def traced_merge(self, event):
        result = original_merge(self, event)
        if getattr(event, "source", None) == progress_status.TRANSFER_SOURCE_ARIA2:
            state = session.current_fast_attempt()
            if state is not None:
                state["ui_merges"] += 1
            session.write(
                "fast_ui_merge",
                input_progress=_progress_event_payload(event),
                output_progress=_progress_event_payload(result),
            )
        return result

    def traced_finish(self):
        session.write("download_ui_finish")
        return original_finish(self)

    window_class._merge_progress_event_for_display = traced_merge
    window_class._finish_download_ui = traced_finish
    session.write("ui_diagnostics_installed")


def _bounded(value: object) -> str:
    try:
        text = str(value or "")
    except Exception:
        return ""
    text = text.replace("\x00", "")
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS] + "<truncated>"


def _canonical_path_text(value: object) -> str:
    try:
        path = Path(value).expanduser().resolve(strict=False)
    except Exception:
        return ""
    return os.path.normcase(str(path))


def _path_fingerprint(value: object) -> str:
    text = _canonical_path_text(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _file_size(value: object) -> int | None:
    try:
        path = Path(value)
        if not path.is_file():
            return None
        return int(path.stat().st_size)
    except OSError:
        return None


def _command_option_value(command: list[str] | tuple[str, ...], option: str) -> str:
    values = list(command)
    for index, value in enumerate(values):
        text = str(value)
        if text == option and index + 1 < len(values):
            return Path(str(values[index + 1])).name if option == "--downloader" else _bounded(values[index + 1])
        prefix = option + "="
        if text.startswith(prefix):
            raw = text[len(prefix) :]
            return Path(raw).name if option == "--downloader" else _bounded(raw)
    return ""


def _looks_like_aria2_progress(value: object) -> bool:
    text = str(value or "")
    return "[#" in text and "%" in text and "]" in text


def _progress_event_payload(event: object) -> dict[str, Any]:
    if dataclasses.is_dataclass(event):
        try:
            return _json_safe(dataclasses.asdict(event))
        except Exception:
            pass
    fields = ("kind", "phase", "message", "video_index", "video_total", "title", "percent", "speed", "eta", "fragment", "source", "generation")
    return {name: _json_safe(getattr(event, name, None)) for name in fields}


def _safe_qsize(value: object) -> int | None:
    try:
        return int(value.qsize())
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return value.name
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return _bounded(value)

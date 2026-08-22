from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


PROBE_BYTES = 32 * 1024 * 1024
PROBE_RATE_BYTES_PER_SECOND = 4 * 1024 * 1024
PROBE_CHUNK_BYTES = 64 * 1024
PROGRESS_PATTERN = re.compile(r"\[#[0-9A-Fa-f]+\s+[^\r\n]*\(\d+%\)[^\r\n]*\]")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
MAX_FRAME_CHARS = 2000


class JsonlWriter:
    """Diagnostic JSONL writer with durable flush after every record."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, event: str, **fields: object) -> None:
        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        with self._lock:
            if self._handle.closed:
                return
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class GlobalRateLimiter:
    def __init__(self, bytes_per_second: int):
        self._bytes_per_second = max(1, int(bytes_per_second))
        self._lock = threading.Lock()
        self._next_slot = time.monotonic()

    def consume(self, byte_count: int) -> None:
        duration = max(0, int(byte_count)) / self._bytes_per_second
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + duration
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class ProbeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "S9HExactBinaryProbe/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._serve(send_body=False)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._serve(send_body=True)

    def _serve(self, *, send_body: bool) -> None:
        if self.path.split("?", 1)[0] != "/probe.mp4":
            self.send_error(404)
            return

        total = int(self.server.probe_bytes)  # type: ignore[attr-defined]
        start = 0
        end = total - 1
        partial = False
        range_header = self.headers.get("Range", "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match:
                self.send_error(416)
                return
            start_text, end_text = match.groups()
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else total - 1
            elif end_text:
                suffix = max(0, int(end_text))
                start = max(0, total - suffix)
                end = total - 1
            if start < 0 or start >= total or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, total - 1)
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Connection", "close")
        self.end_headers()
        if not send_body:
            return

        limiter: GlobalRateLimiter = self.server.rate_limiter  # type: ignore[attr-defined]
        pattern = bytes(range(256)) * (PROBE_CHUNK_BYTES // 256)
        remaining = length
        try:
            while remaining > 0:
                size = min(PROBE_CHUNK_BYTES, remaining)
                limiter.consume(size)
                self.wfile.write(pattern[:size])
                self.wfile.flush()
                remaining -= size
        except (BrokenPipeError, ConnectionResetError):
            return


class ProbeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, probe_bytes: int, bytes_per_second: int):
        super().__init__(("127.0.0.1", 0), ProbeHandler)
        self.probe_bytes = int(probe_bytes)
        self.rate_limiter = GlobalRateLimiter(bytes_per_second)


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_line(path: Path, *args: str) -> str:
    result = subprocess.run(
        [str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        raise RuntimeError(f"Could not query version for {path.name}; exit={result.returncode}")
    return lines[0]


def _load_build_manifest(root: Path) -> dict[str, object]:
    path = root / "DIAGNOSTIC_BUILD.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing diagnostic build marker: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("DIAGNOSTIC_BUILD.json must contain an object")
    source_sha = str(payload.get("source_sha", ""))
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("Diagnostic build marker has an invalid source_sha")
    return payload


def _diagnostic_log_path() -> Path:
    root = Path(tempfile.gettempdir()) / "s9h-ytdl-diag"
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        root.glob("fast-exact-binary-probe-*.jsonl"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for stale in existing[4:]:
        try:
            stale.unlink()
        except OSError:
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return root / f"fast-exact-binary-probe-{stamp}-pid{os.getpid()}.jsonl"


def _split_frames(buffer: bytearray, *, final: bool = False) -> list[bytes]:
    frames: list[bytes] = []
    start = 0
    for index, value in enumerate(buffer):
        if value not in (10, 13):
            continue
        if index > start:
            frames.append(bytes(buffer[start:index]))
        start = index + 1
    if start:
        del buffer[:start]
    if final and buffer:
        frames.append(bytes(buffer))
        buffer.clear()
    return frames


def _clean_frame(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    text = ANSI_ESCAPE_PATTERN.sub("", text).strip()
    if len(text) > MAX_FRAME_CHARS:
        text = text[:MAX_FRAME_CHARS] + "<truncated>"
    return text


def _run_and_capture(
    *,
    label: str,
    command: list[str],
    writer: JsonlWriter,
    cwd: Path,
    timeout_seconds: int = 90,
) -> dict[str, object]:
    safe_command = [Path(command[0]).name, *command[1:]]
    writer.write("process_start", label=label, command=safe_command)
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        bufsize=0,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.stdout is None:
        raise RuntimeError("Subprocess stdout pipe was not created")

    buffer = bytearray()
    frames = 0
    progress_frames = 0
    live_progress_frames = 0
    first_progress_seconds: float | None = None
    deadline = started + timeout_seconds
    try:
        while True:
            if time.monotonic() > deadline:
                process.kill()
                raise TimeoutError(f"{label} timed out after {timeout_seconds}s")
            chunk = process.stdout.read(4096)
            if chunk:
                buffer.extend(chunk)
                for raw_frame in _split_frames(buffer):
                    text = _clean_frame(raw_frame)
                    if not text:
                        continue
                    frames += 1
                    is_progress = bool(PROGRESS_PATTERN.search(text))
                    alive = process.poll() is None
                    if is_progress:
                        progress_frames += 1
                        if alive:
                            live_progress_frames += 1
                        if first_progress_seconds is None:
                            first_progress_seconds = time.monotonic() - started
                    writer.write(
                        "process_frame",
                        label=label,
                        sequence=frames,
                        process_alive=alive,
                        aria2_progress=is_progress,
                        text=text,
                    )
                continue
            if process.poll() is not None:
                break
            time.sleep(0.01)

        for raw_frame in _split_frames(buffer, final=True):
            text = _clean_frame(raw_frame)
            if not text:
                continue
            frames += 1
            is_progress = bool(PROGRESS_PATTERN.search(text))
            if is_progress:
                progress_frames += 1
                if first_progress_seconds is None:
                    first_progress_seconds = time.monotonic() - started
            writer.write(
                "process_frame",
                label=label,
                sequence=frames,
                process_alive=False,
                aria2_progress=is_progress,
                text=text,
            )
    finally:
        if process.poll() is None:
            process.kill()
        return_code = process.wait(timeout=10)

    elapsed = time.monotonic() - started
    result = {
        "return_code": return_code,
        "elapsed_seconds": round(elapsed, 3),
        "frames": frames,
        "progress_frames": progress_frames,
        "live_progress_frames": live_progress_frames,
        "first_progress_seconds": round(first_progress_seconds, 3) if first_progress_seconds is not None else None,
    }
    writer.write("process_exit", label=label, **result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe exact bundled yt-dlp/aria2 binaries without using the application parser or UI."
    )
    parser.add_argument("--root", type=Path, default=None, help="Diagnostic portable root. Defaults to probe EXE folder.")
    args = parser.parse_args()

    root = (args.root or _bundle_root()).resolve()
    manifest = _load_build_manifest(root)
    source_sha = str(manifest["source_sha"])
    bin_root = root / "data" / "bin"
    yt_dlp = bin_root / "yt-dlp.exe"
    aria2 = bin_root / "aria2c.exe"
    for required in (yt_dlp, aria2):
        if not required.is_file():
            raise FileNotFoundError(f"Missing exact bundled runtime binary: {required}")

    log_path = _diagnostic_log_path()
    temp_root = Path(tempfile.mkdtemp(prefix="s9h-fast-exact-probe-"))
    print(f"Diagnostic source SHA: {source_sha}")
    print(f"JSONL: {log_path}")

    server = ProbeServer(PROBE_BYTES, PROBE_RATE_BYTES_PER_SECOND)
    server_thread = threading.Thread(target=server.serve_forever, name="probe-http-server", daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/probe.mp4"

    exit_code = 1
    try:
        with JsonlWriter(log_path) as writer:
            writer.write(
                "probe_start",
                source_sha=source_sha,
                pid=os.getpid(),
                bundle_root=str(root),
                probe_bytes=PROBE_BYTES,
                probe_rate_bytes_per_second=PROBE_RATE_BYTES_PER_SECOND,
            )
            yt_dlp_sha = _sha256(yt_dlp)
            aria2_sha = _sha256(aria2)
            yt_dlp_version = _version_line(yt_dlp, "--version")
            aria2_version = _version_line(aria2, "--version")
            writer.write(
                "binary_identity",
                source_sha=source_sha,
                yt_dlp_sha256=yt_dlp_sha,
                yt_dlp_version=yt_dlp_version,
                aria2_sha256=aria2_sha,
                aria2_version=aria2_version,
                manifest_yt_dlp_sha256=manifest.get("yt_dlp_sha256"),
                manifest_aria2_sha256=manifest.get("aria2_sha256"),
            )

            if manifest.get("yt_dlp_sha256") and str(manifest["yt_dlp_sha256"]).lower() != yt_dlp_sha.lower():
                raise RuntimeError("yt-dlp hash does not match DIAGNOSTIC_BUILD.json")
            if manifest.get("aria2_sha256") and str(manifest["aria2_sha256"]).lower() != aria2_sha.lower():
                raise RuntimeError("aria2 hash does not match DIAGNOSTIC_BUILD.json")

            direct_dir = temp_root / "aria2-direct"
            direct_dir.mkdir(parents=True, exist_ok=True)
            direct = _run_and_capture(
                label="aria2_direct",
                command=[
                    str(aria2),
                    "-c",
                    "--no-conf",
                    "--console-log-level=warn",
                    "--summary-interval=0",
                    "--download-result=hide",
                    "--http-accept-gzip=true",
                    "--file-allocation=none",
                    "--show-console-readout=true",
                    "-x16",
                    "-j16",
                    "-s16",
                    "-k1M",
                    "--dir",
                    str(direct_dir),
                    "--out",
                    "probe-direct.mp4",
                    "--",
                    url,
                ],
                writer=writer,
                cwd=temp_root,
            )

            ytdlp_dir = temp_root / "ytdlp-chain"
            ytdlp_dir.mkdir(parents=True, exist_ok=True)
            chained = _run_and_capture(
                label="ytdlp_aria2_chain",
                command=[
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
                    str(ytdlp_dir / "probe-chain.%(ext)s"),
                    url,
                ],
                writer=writer,
                cwd=temp_root,
            )

            passed = bool(
                direct["return_code"] == 0
                and chained["return_code"] == 0
                and int(direct["live_progress_frames"]) > 0
                and int(chained["live_progress_frames"]) > 0
            )
            writer.write(
                "probe_result",
                source_sha=source_sha,
                passed=passed,
                direct=direct,
                chained=chained,
            )
            exit_code = 0 if passed else 2
    except Exception as exc:
        try:
            with JsonlWriter(log_path) as writer:
                writer.write(
                    "probe_exception",
                    source_sha=source_sha,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
        except Exception:
            pass
        print(f"Probe failed: {type(exc).__name__}: {exc}")
        exit_code = 1
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        try:
            for path in sorted(temp_root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            temp_root.rmdir()
        except OSError:
            pass

    if exit_code == 0:
        print("PASS: exact bundled aria2 output is live both directly and through yt-dlp.")
    elif exit_code == 2:
        print("FAIL: exact-binary progress transport is not live; inspect the JSONL before deeper instrumentation.")
    print(f"JSONL: {log_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

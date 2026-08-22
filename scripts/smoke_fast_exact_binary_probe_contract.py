from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "scripts" / "diagnose_fast_exact_binaries.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("diagnose_fast_exact_binaries", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load exact-binary probe module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    probe = _load_probe()

    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "probe.jsonl"
        writer = probe.JsonlWriter(log_path)
        writer.write("first", source_sha="a" * 40, sequence=1)
        first_size = log_path.stat().st_size
        if first_size <= 0:
            raise AssertionError("First JSONL record was not visible on disk immediately")
        writer.write("second", source_sha="a" * 40, sequence=2)
        second_size = log_path.stat().st_size
        if second_size <= first_size:
            raise AssertionError("Second JSONL record was not flushed immediately")
        writer.close()

        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        if [row["event"] for row in rows] != ["first", "second"]:
            raise AssertionError("JSONL event order changed")
        if any(row.get("source_sha") != "a" * 40 for row in rows):
            raise AssertionError("Diagnostic records lost the source SHA")

    frames = probe._split_frames(bytearray(b"one\rtwo\nthree\r\nfour"), final=True)
    decoded = [frame.decode("ascii") for frame in frames]
    if decoded != ["one", "two", "three", "four"]:
        raise AssertionError(f"CR/LF framing changed: {decoded!r}")

    if not probe.PROGRESS_PATTERN.search("[#abc123 2MiB/10MiB(20%) CN:4 DL:3MiB ETA:2s]"):
        raise AssertionError("aria2 progress pattern no longer matches expected raw output")

    print("fast exact-binary probe contract smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

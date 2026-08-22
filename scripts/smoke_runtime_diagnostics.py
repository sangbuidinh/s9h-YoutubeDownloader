from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import runtime_diagnostics as diagnostics


def main() -> int:
    original_app_root = diagnostics.app_root
    original_gettempdir = diagnostics.tempfile.gettempdir
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app_dir = root / "app"
            temp_dir = root / "temp"
            app_dir.mkdir()
            temp_dir.mkdir()

            diagnostics.app_root = lambda: app_dir
            diagnostics.tempfile.gettempdir = lambda: str(temp_dir)

            if diagnostics.load_diagnostic_session() is not None:
                raise AssertionError("Diagnostics activated without DIAGNOSTIC_BUILD.json")

            source_sha = "a" * 40
            marker = {
                "schema_version": 1,
                "purpose": "smoke-runtime-diagnostics",
                "source_sha": source_sha,
                "workflow_run_id": "smoke",
                "workflow_run_attempt": "1",
            }
            (app_dir / diagnostics.MARKER_NAME).write_text(
                json.dumps(marker),
                encoding="utf-8",
            )

            session = diagnostics.load_diagnostic_session()
            if session is None:
                raise AssertionError("Valid SHA-tagged marker did not activate diagnostics")
            if session.source_sha != source_sha:
                raise AssertionError("Diagnostic source SHA changed")
            if not session.path.is_file() or session.path.stat().st_size <= 0:
                raise AssertionError("session_start record was not flushed to disk immediately")

            before = session.path.stat().st_size
            session.write("flush_probe", checkpoint="visible-before-close")
            after = session.path.stat().st_size
            if after <= before:
                raise AssertionError("JSONL record was not flushed before close")

            live_rows = [
                json.loads(line)
                for line in session.path.read_text(encoding="utf-8").splitlines()
            ]
            if live_rows[-1].get("event") != "flush_probe":
                raise AssertionError("Last flushed event was not readable while session remained open")
            if any(row.get("source_sha") != source_sha for row in live_rows):
                raise AssertionError("A runtime diagnostic record lost source_sha")

            session.close()
            closed_rows = [
                json.loads(line)
                for line in session.path.read_text(encoding="utf-8").splitlines()
            ]
            if closed_rows[-1].get("event") != "session_end":
                raise AssertionError("session_end was not persisted")

            (app_dir / diagnostics.MARKER_NAME).write_text(
                json.dumps({"source_sha": "short"}),
                encoding="utf-8",
            )
            if diagnostics.load_diagnostic_session() is not None:
                raise AssertionError("Invalid source SHA unexpectedly activated diagnostics")
    finally:
        diagnostics.app_root = original_app_root
        diagnostics.tempfile.gettempdir = original_gettempdir

    print("runtime diagnostics smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

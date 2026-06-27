import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_utils import local_log_timestamp, timestamp_log_lines


def main() -> int:
    fixed = datetime(
        2026,
        6,
        27,
        16,
        5,
        4,
        123456,
        tzinfo=timezone(timedelta(hours=7)),
    )
    expected_prefix = "[2026-06-27 16:05:04.123 +07:00]"

    assert local_log_timestamp(fixed) == expected_prefix

    lines = timestamp_log_lines("[INFO] First\n[ERROR] Second", fixed)
    assert lines == [
        ("[INFO] First", f"{expected_prefix} [INFO] First"),
        ("[ERROR] Second", f"{expected_prefix} [ERROR] Second"),
    ]

    live = local_log_timestamp()
    assert re.fullmatch(
        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{2}:\d{2}\]",
        live,
    ), live

    print("log timestamp smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from datetime import datetime


def local_log_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()

    offset = current.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    elif not offset:
        offset = "+00:00"

    milliseconds = current.strftime("%f")[:3]
    return f"[{current:%Y-%m-%d %H:%M:%S}.{milliseconds} {offset}]"


def timestamp_log_lines(
    message: object,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    text = str(message or "")
    source_lines = text.splitlines() or [""]
    timestamp = local_log_timestamp(now)
    return [
        (line, f"{timestamp} {line}" if line else timestamp)
        for line in source_lines
    ]

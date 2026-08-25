from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs/release_notes_v1.3.2.md"


def main() -> int:
    raw = NOTES.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n"), "release notes bytes are not canonical UTF-8/LF")
    text = raw.decode("utf-8")
    normalized = " ".join(text.split())
    required = (
        "82.18 seconds total",
        "22.45 seconds preparation",
        "53.00 seconds transfer",
        "approximately 84.5 seconds wall time",
        "70.97 seconds total",
        "11.48 seconds preparation",
        "52.83 seconds transfer",
        "74.265 seconds wall time",
        "11.21-second (13.64%) saving",
        "Most of the measured improvement was in preparation time",
        "transfer time was approximately unchanged",
        "not a universal speed guarantee",
        "Downloaded state is video-scoped",
        "Changing numbering or the Save folder alone does not authorize a re-download",
        "Manually setting `Chưa tải` remains an explicit re-download override",
        "missing-part state repairs only the required missing outputs",
        "metadata extraction",
        "native yt-dlp video transport",
    )
    for phrase in required:
        _require(phrase in normalized, f"release notes are missing: {phrase}")
    _require("Production performance acceptance remains pending" not in normalized, "obsolete performance-pending claim remains")
    _require("IDM" not in normalized and "XDM" not in normalized, "unshipped research transport appears in release notes")
    print("v1.3.2 release notes smoke tests passed")
    return 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
HISTORY_PATH = REPO_ROOT / "docs" / "history" / "phase-3h8-one-video-lookahead.md"
BASELINE_COMMIT = "cfd6f050a18f18ef5760104051080f936383d2b9"
ARCHIVE_HEADER = (
    "> Historical implementation note archived from the repository root\n"
    "> README. It describes Phase 3H.8 and is not the current product guide.\n"
    "\n"
    "---\n"
    "\n"
).encode("utf-8")


def main() -> int:
    readme = README_PATH.read_text(encoding="utf-8")
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    _assert(not readme.startswith("# Phase 3H.8"), "root README is still the Phase 3H.8 note")
    _assert(readme.startswith("# Youtube Downloaderbs\n"), "root README is not product-focused")
    _assert(f"Current stable version: `v{version}`" in readme, "README version does not match VERSION")
    _assert(f"Youtube-Downloaderbs-v{version}.zip" in readme, "portable ZIP does not match VERSION")
    _assert("The standalone EXE is not a complete fresh installation" in readme, "standalone EXE distinction is missing")

    for mode in ("Video + Thumb", "Audio MP3 + Thumb", "Video + Audio MP3 + Thumb"):
        _assert(mode in readme, f"download mode is missing: {mode}")
    for required in ("Stable - yt-dlp internal", "Fast - aria2c experimental", "1080p", "H.264", "AAC"):
        _assert(required in readme, f"required product wording is missing: {required}")

    _assert(re.search(r"File start number.{0,220}session-only", readme, re.IGNORECASE | re.DOTALL), "session-only numbering is missing")
    _assert(re.search(r"API key.{0,500}(sensitive|Never commit)", readme, re.IGNORECASE | re.DOTALL), "API key safety is missing")
    _assert(re.search(r"Cookies.{0,500}sensitive", readme, re.IGNORECASE | re.DOTALL), "cookie sensitivity is missing")

    _assert(re.search(r"[A-Za-z]:\\", readme) is None, "README contains a local absolute Windows path")
    for pattern, label in (
        (r"AIza[0-9A-Za-z_-]{30,}", "probable YouTube API key"),
        (r"ghp_[0-9A-Za-z]{20,}", "probable GitHub token"),
        (r"github_pat_[0-9A-Za-z_]{20,}", "probable GitHub fine-grained token"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key header"),
        (r"https?://[^\s]+googlevideo\.com[^\s]*", "signed media URL"),
    ):
        _assert(re.search(pattern, readme) is None, f"README contains {label}")

    _verify_relative_links(readme)
    _verify_historical_archive()
    print("product README smoke tests passed")
    return 0


def _verify_relative_links(readme: str) -> None:
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
    checked: set[str] = set()
    for target in links:
        clean_target = target.strip().split("#", 1)[0]
        if not clean_target or clean_target.startswith(("#", "https://", "http://", "mailto:")):
            continue
        _assert(not Path(clean_target).is_absolute(), f"README link is absolute: {target}")
        _assert((REPO_ROOT / clean_target).exists(), f"README link does not exist: {target}")
        checked.add(clean_target)
    expected = {
        "docs/ui_logic_contract.md",
        "docs/release_notes_v1.3.1.md",
        "docs/history/phase-3h8-one-video-lookahead.md",
    }
    _assert(expected.issubset(checked), f"required documentation links are missing: {sorted(expected - checked)}")


def _verify_historical_archive() -> None:
    _assert(HISTORY_PATH.is_file(), "historical Phase 3H.8 file is missing")
    archived = HISTORY_PATH.read_bytes()
    _assert(archived.startswith(ARCHIVE_HEADER), "historical archive metadata header changed")
    archived_body = archived[len(ARCHIVE_HEADER) :]
    baseline_body = subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:README.md"],
        cwd=REPO_ROOT,
    ).replace(b"\r\n", b"\n")
    _assert(archived_body == baseline_body, "historical Phase 3H.8 body does not match baseline README")
    _assert(b"# Phase 3H.8" in archived_body, "historical Phase 3H.8 title is missing")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

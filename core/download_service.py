"""Public application facade for download validation and execution.

The desktop UI depends on this module instead of the downloader implementation.
Implementation-oriented scripts may continue using ``core.downloader`` while its
legacy public imports are retained during the Phase 8 decomposition.
"""

from core.download_contracts import (
    BatchDecision,
    COOKIE_SOURCE_BRIDGE,
    COOKIE_SOURCE_FILE,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadError,
    DownloadOptions,
    SystemicBlockContext,
)
from core.downloader import (
    DownloadController,
    download_items,
    validate_download_environment,
    validate_file_start_number,
    validate_speed_limit,
)


__all__ = (
    "BatchDecision",
    "COOKIE_SOURCE_BRIDGE",
    "COOKIE_SOURCE_FILE",
    "DOWNLOAD_ENGINE_ARIA2_FAST",
    "DOWNLOAD_ENGINE_STABLE",
    "DownloadController",
    "DownloadError",
    "DownloadOptions",
    "SystemicBlockContext",
    "download_items",
    "validate_download_environment",
    "validate_file_start_number",
    "validate_speed_limit",
)

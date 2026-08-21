import ast
import importlib
import sys
from dataclasses import fields
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONTRACTS_PATH = REPO_ROOT / "core" / "download_contracts.py"
SERVICE_PATH = REPO_ROOT / "core" / "download_service.py"
DOWNLOADER_PATH = REPO_ROOT / "core" / "downloader.py"
PROCESS_PATH = REPO_ROOT / "core" / "download_process.py"
YTDLP_COMMANDS_PATH = REPO_ROOT / "core" / "ytdlp_commands.py"
FFMPEG_TOOLS_PATH = REPO_ROOT / "core" / "ffmpeg_tools.py"
UI_PATH = REPO_ROOT / "ui" / "main_window.py"
UI_SESSION_PATH = REPO_ROOT / "ui" / "session_state.py"
UI_BACKGROUND_PATH = REPO_ROOT / "ui" / "background_tasks.py"
FOCUSED_MODULE_NAMES = (
    "core.download_process",
    "core.ytdlp_commands",
    "core.ffmpeg_tools",
)


def main() -> int:
    contracts = importlib.import_module("core.download_contracts")
    _assert_not_loaded("tkinter", "download contracts loaded Tkinter")

    process = importlib.import_module("core.download_process")
    ytdlp_commands = importlib.import_module("core.ytdlp_commands")
    ffmpeg_tools = importlib.import_module("core.ffmpeg_tools")
    _assert_not_loaded("tkinter", "focused download modules loaded Tkinter")
    _assert(
        "core.downloader" not in sys.modules,
        "focused download modules imported the orchestration module",
    )

    service = importlib.import_module("core.download_service")
    downloader = importlib.import_module("core.downloader")
    _assert_not_loaded("tkinter", "download service loaded Tkinter")
    session_state = importlib.import_module("ui.session_state")
    background_tasks = importlib.import_module("ui.background_tasks")
    _assert_not_loaded("tkinter", "UI session/background modules loaded Tkinter")
    _assert(
        "ui.main_window" not in sys.modules,
        "UI session/background modules imported the Tkinter window",
    )

    _test_public_contracts(
        contracts,
        service,
        downloader,
        process,
        ytdlp_commands,
        ffmpeg_tools,
    )
    _test_ui_session_contracts(session_state)
    _test_dependency_direction()
    print("download architecture smoke passed")
    return 0


def _test_public_contracts(
    contracts,
    service,
    downloader,
    process,
    ytdlp_commands,
    ffmpeg_tools,
) -> None:
    _assert(contracts.DOWNLOAD_ENGINE_STABLE == "stable", "Stable engine identifier changed")
    _assert(
        contracts.DOWNLOAD_ENGINE_ARIA2_FAST == "aria2_fast",
        "Fast engine identifier changed",
    )
    _assert(
        contracts.DEFAULT_DOWNLOAD_ENGINE == contracts.DOWNLOAD_ENGINE_STABLE,
        "Default engine is no longer Stable",
    )

    expected_option_fields = (
        "base_folder",
        "channel_id",
        "channel_name",
        "cookies_enabled",
        "cookies_path",
        "speed_limit",
        "download_mode",
        "cookie_source",
        "bridge_cookie_path",
        "download_engine",
        "file_start_number",
    )
    _assert(
        tuple(field.name for field in fields(contracts.DownloadOptions))
        == expected_option_fields,
        "DownloadOptions fields changed",
    )

    compatibility_symbols = (
        "BatchDecision",
        "DownloadCancelled",
        "DownloadError",
        "DownloadOptions",
        "FFmpegFailureKind",
        "SkipCurrentVideo",
        "SystemicBlockContext",
        "YtdlpFailureKind",
    )
    for name in compatibility_symbols:
        _assert(
            getattr(downloader, name) is getattr(contracts, name),
            f"core.downloader compatibility import changed identity: {name}",
        )

    facade_contracts = (
        "BatchDecision",
        "DownloadError",
        "DownloadOptions",
        "SystemicBlockContext",
    )
    for name in facade_contracts:
        _assert(
            getattr(service, name) is getattr(contracts, name),
            f"download facade contract changed identity: {name}",
        )

    facade_operations = (
        "DownloadController",
        "download_items",
        "validate_download_environment",
        "validate_file_start_number",
        "validate_speed_limit",
    )
    for name in facade_operations:
        _assert(
            getattr(service, name) is getattr(downloader, name),
            f"download facade operation changed identity: {name}",
        )

    _assert(
        downloader.DownloadController is process.DownloadController,
        "legacy downloader controller import changed identity",
    )
    _assert(
        downloader.FFmpegExecutionError is ffmpeg_tools.FFmpegExecutionError,
        "legacy downloader FFmpeg error import changed identity",
    )
    _assert(
        downloader.classify_ffmpeg_failure_kind
        is ffmpeg_tools.classify_ffmpeg_failure_kind,
        "legacy downloader FFmpeg classification import changed identity",
    )
    _assert(
        downloader._parse_ffmpeg_progress_line
        is ffmpeg_tools._parse_ffmpeg_progress_line,
        "legacy downloader FFmpeg parser import changed identity",
    )
    _assert(
        downloader._terminate_process_tree is process._terminate_process_tree,
        "legacy downloader process helper import changed identity",
    )
    _assert(
        downloader._sleep_with_cancel is process._sleep_with_cancel,
        "legacy downloader cancellation helper import changed identity",
    )
    _assert(
        downloader._safe_temp_stem is ytdlp_commands._safe_temp_stem,
        "legacy downloader yt-dlp helper import changed identity",
    )
    _assert(
        downloader._Aria2RuntimeValidation
        is ytdlp_commands._Aria2RuntimeValidation,
        "legacy downloader aria2 validation import changed identity",
    )
    _assert(
        downloader.PREMIERE_SAFE_VIDEO_FORMAT
        == ytdlp_commands.PREMIERE_SAFE_VIDEO_FORMAT,
        "legacy downloader video format selector changed",
    )
    _assert(
        downloader.ARIA2_FAST_DOWNLOADER_ARGS
        == ytdlp_commands.ARIA2_FAST_DOWNLOADER_ARGS,
        "legacy downloader Fast arguments changed",
    )


def _test_dependency_direction() -> None:
    contracts_tree = _module_tree(CONTRACTS_PATH)
    service_tree = _module_tree(SERVICE_PATH)
    downloader_tree = _module_tree(DOWNLOADER_PATH)
    focused_trees = {
        "core.download_process": _module_tree(PROCESS_PATH),
        "core.ytdlp_commands": _module_tree(YTDLP_COMMANDS_PATH),
        "core.ffmpeg_tools": _module_tree(FFMPEG_TOOLS_PATH),
    }
    ui_tree = _module_tree(UI_PATH)
    ui_session_tree = _module_tree(UI_SESSION_PATH)
    ui_background_tree = _module_tree(UI_BACKGROUND_PATH)

    _assert_no_import_prefix(
        contracts_tree,
        ("tkinter", "subprocess", "urllib", "core.state_store", "ui"),
    )
    _assert_no_import_prefix(service_tree, ("tkinter", "ui"))
    _assert_no_import_prefix(downloader_tree, ("ui", "core.download_service"))
    for tree in focused_trees.values():
        _assert_no_import_prefix(
            tree,
            ("tkinter", "ui", "core.download_service", "core.downloader"),
        )
    _assert_no_import_prefix(
        ui_session_tree,
        ("tkinter", "core", "ui.main_window", "ui.background_tasks"),
    )
    _assert_no_import_prefix(
        ui_background_tree,
        ("tkinter", "ui.main_window"),
    )

    service_imports = _imported_modules(service_tree)
    downloader_imports = _imported_modules(downloader_tree)
    ui_imports = _imported_modules(ui_tree)
    ui_background_imports = _imported_modules(ui_background_tree)
    _assert("core.download_contracts" in service_imports, "facade does not expose contracts")
    _assert("core.downloader" in service_imports, "facade does not reach downloader implementation")
    _assert("core.download_contracts" in downloader_imports, "downloader does not use contract ownership")
    _assert("core.download_service" in ui_imports, "UI does not use the download facade")
    _assert("core.downloader" not in ui_imports, "UI still imports downloader implementation")
    for module in FOCUSED_MODULE_NAMES:
        _assert(module not in ui_imports, f"UI imports focused implementation module {module}")
        _assert(
            module not in ui_background_imports,
            f"UI background tasks import focused implementation module {module}",
        )
    _assert("ui.session_state" in ui_imports, "main window does not use UI session state")
    _assert("ui.background_tasks" in ui_imports, "main window does not use UI background tasks")
    _assert(
        "ui.session_state" in ui_background_imports,
        "background tasks do not use immutable UI request contracts",
    )
    _assert_acyclic_ui_modules(ui_tree, ui_session_tree, ui_background_tree)

    downloader_imports = _imported_modules(downloader_tree)
    for module in FOCUSED_MODULE_NAMES:
        _assert(module in downloader_imports, f"downloader does not use focused module {module}")
    _assert_acyclic_download_modules(downloader_tree, focused_trees)

    moved_names = {
        "BatchDecision",
        "COOKIE_SOURCE_BRIDGE",
        "COOKIE_SOURCE_FILE",
        "DEFAULT_DOWNLOAD_ENGINE",
        "DOWNLOAD_ENGINE_ARIA2_FAST",
        "DOWNLOAD_ENGINE_STABLE",
        "DownloadCancelled",
        "DownloadError",
        "DownloadOptions",
        "FFmpegFailureKind",
        "SkipCurrentVideo",
        "SystemicBlockContext",
        "YtdlpFailureKind",
        "DownloadController",
        "FFmpegExecutionError",
        "_Aria2RuntimeValidation",
        "_FfmpegProgressState",
        "_normalize_download_engine",
        "_safe_temp_stem",
        "_parse_ffmpeg_progress_line",
        "classify_ffmpeg_failure_kind",
    }
    downloader_owned_names = _top_level_owned_names(downloader_tree)
    _assert(
        moved_names.isdisjoint(downloader_owned_names),
        "downloader duplicates focused-module or contract ownership",
    )


def _test_ui_session_contracts(session_state) -> None:
    context = session_state.ChannelRequestContext(
        save_folder="snapshot",
        download_mode="Video + Thumb",
        hide_below_enabled=True,
        hide_below_minutes=3,
        hide_above_enabled=True,
        hide_above_minutes=60,
    )
    requests = session_state.ChannelRequestState()
    stale_fetch = requests.begin_fetch("old-channel", context, "old-key")
    current_fetch = requests.begin_fetch("new-channel", context, "current-key")
    _assert(not requests.is_current_fetch(stale_fetch), "stale Fetch token remained current")
    _assert(requests.is_current_fetch(current_fetch), "current Fetch token was rejected")
    requests.accept_fetch(current_fetch)
    _assert(
        requests.take_accepted_manual_key(current_fetch) == "current-key",
        "accepted Fetch did not retain its matching manual-key persistence claim",
    )

    load_more = requests.begin_load_more(
        channel_id="channel-id",
        uploads_playlist_id="uploads-id",
        page_token="page-1",
        start_order=101,
        context=context,
    )
    snapshot = {
        "channel_id": "channel-id",
        "uploads_playlist_id": "uploads-id",
        "page_token": "page-1",
        "start_order": 101,
    }
    _assert(
        requests.is_current_load_more(load_more, **snapshot),
        "matching Load More snapshot was rejected",
    )
    _assert(
        not requests.is_current_load_more(load_more, **{**snapshot, "page_token": "page-2"}),
        "stale Load More page snapshot was accepted",
    )
    requests.begin_fetch("newer-channel", context, "")
    _assert(
        not requests.is_current_load_more(load_more, **snapshot),
        "old Load More token survived a newer channel generation",
    )

    downloads = session_state.DownloadSessionState()
    first_run = downloads.begin_run(51, {"A", "B"}, {"A"})
    _assert(downloads.record_completion(first_run, "A") is None, "initially complete item advanced numbering")
    _assert(downloads.record_completion(first_run, "B") == 52, "new completion did not advance numbering")
    _assert(downloads.record_completion(first_run, "B") is None, "duplicate completion advanced numbering")
    second_run = downloads.begin_run(100, {"C"}, set())
    _assert(downloads.record_completion(first_run, "B") is None, "stale download run changed numbering")
    _assert(downloads.record_completion(second_run, "C") == 101, "new download run used stale numbering")


def _assert_acyclic_ui_modules(
    main_window_tree: ast.Module,
    session_tree: ast.Module,
    background_tree: ast.Module,
) -> None:
    module_trees = {
        "ui.main_window": main_window_tree,
        "ui.session_state": session_tree,
        "ui.background_tasks": background_tree,
    }
    graph = {
        name: _imported_modules(tree).intersection(module_trees)
        for name, tree in module_trees.items()
    }
    _assert_acyclic_graph(graph, "circular UI module import includes")


def _assert_acyclic_download_modules(
    downloader_tree: ast.Module,
    focused_trees: dict[str, ast.Module],
) -> None:
    module_trees = {"core.downloader": downloader_tree, **focused_trees}
    graph = {
        name: _imported_modules(tree).intersection(module_trees)
        for name, tree in module_trees.items()
    }
    _assert_acyclic_graph(graph, "circular download module import includes")


def _assert_acyclic_graph(graph: dict[str, set[str]], message: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        _assert(name not in visiting, f"{message} {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for module in graph:
        visit(module)


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assert_no_import_prefix(tree: ast.Module, forbidden: tuple[str, ...]) -> None:
    imported = _imported_modules(tree)
    for module in imported:
        for prefix in forbidden:
            _assert(
                module != prefix and not module.startswith(f"{prefix}."),
                f"forbidden dependency {module} imported",
            )


def _top_level_owned_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _assert_not_loaded(module_prefix: str, message: str) -> None:
    _assert(
        not any(
            name == module_prefix or name.startswith(f"{module_prefix}.")
            for name in sys.modules
        ),
        message,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())

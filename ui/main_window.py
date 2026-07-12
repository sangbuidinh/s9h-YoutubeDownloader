import queue
import sys
import threading
import tkinter as tk
import time
import urllib.parse
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, font, ttk

from core.download_modes import DOWNLOAD_MODES, MODE_VIDEO_THUMB
from core.downloader import (
    BatchDecision,
    COOKIE_SOURCE_BRIDGE,
    COOKIE_SOURCE_FILE,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadController,
    DownloadError,
    DownloadOptions,
    SystemicBlockContext,
    validate_download_environment,
    validate_file_start_number,
    validate_speed_limit,
    download_items,
)
from core.app_settings import (
    load_api_key_persistence_state,
    load_bridge_cookie_path,
    load_cookie_source,
    load_cookies_path,
    save_bridge_cookie_path,
    save_cookie_preferences,
    save_cookie_source,
    save_cookies_path,
    save_last_api_key,
)
from core.error_messages import classify_api_error, classify_general_error, format_friendly_error
from core.file_status import apply_statuses, build_output_paths, should_show_not_downloaded
from core.logging_utils import timestamp_log_lines
from core.progress_status import ProgressEvent, format_progress_event_lines, put_latest_progress_event
from core.state_store import (
    STATUS_DOWNLOADED,
    STATUS_MISSING_AUDIO,
    STATUS_MISSING_AUDIO_THUMB,
    STATUS_MISSING_THUMB,
    STATUS_MISSING_VIDEO,
    STATUS_MISSING_VIDEO_AUDIO,
    STATUS_MISSING_VIDEO_THUMB,
    STATUS_NOT_DOWNLOADED,
    SUPPORTED_STATUS_VALUES,
    clear_manual_status,
    get_video_entry,
    is_mode_complete,
    update_manual_status,
)
from core.youtube_api import (
    DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
    DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    YoutubeApiError,
    fetch_latest_video_page,
    fetch_more_videos,
    is_video_visible_by_duration,
    sanitize_log_text,
)
from ui.dialogs import (
    DialogContext,
    show_app_dialog,
    show_confirm_dialog,
    show_copy_text_dialog,
    show_error_dialog,
)


FILTER_ALL = "Hiển thị tất cả"
FILTER_NOT_DOWNLOADED = "Chỉ hiển thị video chưa tải"
DURATION_FILTER_MIN_MINUTES = 1
DURATION_FILTER_MAX_MINUTES = 9999
DURATION_FILTER_MAX_DIGITS = 4
DEFAULT_HIDE_BELOW_MINUTES = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS // 60
DEFAULT_HIDE_ABOVE_MINUTES = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS // 60
COOKIE_STATUS_POLL_MS = 4000
SHUTDOWN_POLL_MS = 150
SHUTDOWN_SLOW_WARNING_SECONDS = 10
TREE_COLUMN_IDS = ("selected", "title", "duration", "published", "status")
TREE_DATA_COLUMNS = ("title", "duration", "published", "status")
TREE_FIXED_COLUMNS = ("selected",)
TREE_COLUMN_DEFAULTS = {
    "selected": {"width": 42, "minwidth": 35, "stretch": False, "anchor": "center"},
    "title": {"width": 720, "minwidth": 300, "stretch": False, "anchor": "w"},
    "duration": {"width": 115, "minwidth": 90, "stretch": False, "anchor": "center"},
    "published": {"width": 140, "minwidth": 115, "stretch": False, "anchor": "center"},
    "status": {"width": 165, "minwidth": 140, "stretch": False, "anchor": "center"},
}
COOKIE_SOURCE_LABELS = {
    COOKIE_SOURCE_FILE: "File cookies.txt",
    COOKIE_SOURCE_BRIDGE: "Local Cookie Bridge",
}
COOKIE_SOURCE_VALUES_BY_LABEL = {label: value for value, label in COOKIE_SOURCE_LABELS.items()}
DOWNLOAD_ENGINE_LABELS = {
    DOWNLOAD_ENGINE_STABLE: "Stable - yt-dlp internal",
    DOWNLOAD_ENGINE_ARIA2_FAST: "Fast - aria2c experimental",
}
DOWNLOAD_ENGINE_VALUES_BY_LABEL = {label: value for value, label in DOWNLOAD_ENGINE_LABELS.items()}
PREFERRED_INITIAL_WINDOW_WIDTH = 1440
PREFERRED_INITIAL_WINDOW_HEIGHT = 700
MINIMUM_WINDOW_WIDTH = 1000
MINIMUM_WINDOW_HEIGHT = 640
INITIAL_WINDOW_CONTENT_PADDING = 8


def _initial_window_size(
    requested_height: int,
    screen_width: int,
    screen_height: int,
    maximum_width: int,
    maximum_height: int,
) -> tuple[int, int]:
    available_width = max(
        1,
        min(max(1, int(screen_width)), max(1, int(maximum_width))),
    )
    available_height = max(
        1,
        min(max(1, int(screen_height)), max(1, int(maximum_height))),
    )
    target_width = min(PREFERRED_INITIAL_WINDOW_WIDTH, available_width)
    content_height = max(
        PREFERRED_INITIAL_WINDOW_HEIGHT,
        max(1, int(requested_height)) + INITIAL_WINDOW_CONTENT_PADDING,
    )
    target_height = min(content_height, available_height)
    return target_width, target_height


@dataclass(frozen=True)
class _ChannelRequestContext:
    save_folder: str
    download_mode: str
    hide_below_enabled: bool
    hide_below_minutes: int
    hide_above_enabled: bool
    hide_above_minutes: int


@dataclass(frozen=True)
class _FetchRequestToken:
    generation: int
    request_id: int
    channel_input: str
    context: _ChannelRequestContext


@dataclass(frozen=True)
class _LoadMoreRequestToken:
    generation: int
    request_id: int
    channel_id: str
    uploads_playlist_id: str
    page_token: str
    start_order: int
    context: _ChannelRequestContext


class YouTubeDownloaderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube Downloaderbs")
        self._app_icon_image: tk.PhotoImage | None = None
        self._apply_window_icon()
        self.root.minsize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)

        self.events: queue.Queue = queue.Queue()
        self.progress_queue: queue.Queue = queue.Queue(maxsize=1)
        self.channel_info = None
        self.videos = []
        self.selected_orders: set[int] = set()
        self.visible_orders: list[int] = []
        self.next_page_token = ""
        self.fetching = False
        self.loading_more = False
        self._channel_generation = 0
        self._channel_request_sequence = 0
        self._active_fetch_request: _FetchRequestToken | None = None
        self._active_load_more_request: _LoadMoreRequestToken | None = None
        self._active_fetch_manual_key = ""
        self._active_fetch_manual_key_request_id: int | None = None
        self._loaded_channel_generation: int | None = None
        self._loaded_channel_context: _ChannelRequestContext | None = None
        self.downloading = False
        self.download_controller: DownloadController | None = None
        self.download_worker: threading.Thread | None = None
        self.download_stop_requested = False
        self.exit_after_download_stop = False
        self.close_requested = False
        self.cancel_download = False
        self.shutdown_in_progress = False
        self.shutdown_started_at: float | None = None
        self._shutdown_poll_after_id = None
        self._download_finish_poll_after_id = None
        self._root_destroyed = False
        self._shutdown_slow_warning_logged = False
        self._shutdown_cancel_reissued = False
        self._download_terminal_received = False
        self._download_terminal_outcome = ""
        self._download_terminal_message = ""
        self._download_run_sequence = 0
        self._active_download_run_id: int | None = None
        self._download_run_start_number: int | None = None
        self._download_run_selected_ids: set[str] = set()
        self._download_run_initial_complete_ids: set[str] = set()
        self._download_run_completed_ids: set[str] = set()
        self.status_editor = None

        api_key_state = load_api_key_persistence_state()
        self._api_key_storage_available = api_key_state.storage_available
        self._api_key_persistence_status = api_key_state.status

        self.api_key_var = tk.StringVar(value=api_key_state.api_key)
        self.channel_var = tk.StringVar()
        self.channel_display_var = tk.StringVar(value="Hiển thị: -")
        self.save_folder_var = tk.StringVar()
        self.cookies_enabled_var = tk.BooleanVar(value=False)
        self.cookies_path_var = tk.StringVar(value=load_cookies_path())
        self.cookie_source_var = tk.StringVar(value=COOKIE_SOURCE_LABELS[load_cookie_source()])
        self.bridge_cookie_path_var = tk.StringVar(value=load_bridge_cookie_path())
        self.cookie_status_var = tk.StringVar()
        self.speed_limit_var = tk.StringVar()
        self.file_start_number_var = tk.StringVar(value="")
        self.download_mode_var = tk.StringVar(value=MODE_VIDEO_THUMB)
        self.download_engine_var = tk.StringVar(value=DOWNLOAD_ENGINE_LABELS[DOWNLOAD_ENGINE_STABLE])
        self.hide_below_enabled_var = tk.BooleanVar(value=True)
        self.hide_below_minutes_var = tk.StringVar(value=str(DEFAULT_HIDE_BELOW_MINUTES))
        self.hide_above_enabled_var = tk.BooleanVar(value=True)
        self.hide_above_minutes_var = tk.StringVar(value=str(DEFAULT_HIDE_ABOVE_MINUTES))
        self.filter_var = tk.StringVar(value=FILTER_ALL)
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar()
        self.progress_current_var = tk.StringVar(value="Đang tải: Sẵn sàng")
        self.progress_detail_var = tk.StringVar(value="Đang xử lý: -")
        self._reset_progress_sticky(reset_order=True)
        self.search_match_orders: list[int] = []
        self.current_search_match_index = -1
        self.tree_column_drag: dict | None = None
        self.tree_column_ratios: dict[str, float] = self._default_tree_column_ratios()
        self.tree_column_fit_after_id = None
        self.tree_column_fit_in_progress = False

        self._build_ui()
        self._fit_initial_window_to_content()
        self._log_api_key_persistence_startup_status()
        self.channel_var.trace_add("write", lambda *_args: self._update_channel_input_display())
        self.search_var.trace_add("write", lambda *_args: self._on_search_text_changed())
        self.hide_below_minutes_var.trace_add("write", self._on_duration_filter_text_changed)
        self.hide_above_minutes_var.trace_add("write", self._on_duration_filter_text_changed)
        self.cookies_path_var.trace_add("write", lambda *_args: self._refresh_cookie_status())
        self.bridge_cookie_path_var.trace_add("write", lambda *_args: self._refresh_cookie_status())
        self._update_channel_input_display()
        self._update_cookies_state()
        self._refresh_cookie_status()
        self._update_download_button_text()
        self._update_more_button_state()
        self._update_stop_button_state()
        self._refresh_interaction_control_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_events)
        self.root.after(300, self._poll_progress_queue)
        self.root.after(COOKIE_STATUS_POLL_MS, self._poll_cookie_status)

    def _find_icon_asset(self, filename: str) -> Path | None:
        candidates: list[Path] = []
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            candidates.append(Path(bundle_root) / "assets" / filename)
        candidates.append(Path(__file__).resolve().parents[1] / "assets" / filename)
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / "assets" / filename)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _apply_window_icon(self) -> None:
        try:
            ico_path = self._find_icon_asset("app_icon.ico")
            if ico_path and sys.platform.startswith("win"):
                try:
                    self.root.iconbitmap(default=str(ico_path))
                except Exception:
                    pass

            png_path = self._find_icon_asset("app_icon.png")
            if png_path:
                self._app_icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._app_icon_image)
        except Exception:
            self._app_icon_image = None

    def _configure_ui_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        available_fonts = set(font.families(self.root))
        ui_family = "Segoe UI" if "Segoe UI" in available_fonts else font.nametofont("TkDefaultFont").actual("family")
        mono_family = next(
            (candidate for candidate in ("Cascadia Mono", "Consolas", "Courier New") if candidate in available_fonts),
            font.nametofont("TkFixedFont").actual("family"),
        )
        font.nametofont("TkDefaultFont").configure(family=ui_family, size=9)
        font.nametofont("TkTextFont").configure(family=ui_family, size=9)
        font.nametofont("TkMenuFont").configure(family=ui_family, size=9)
        font.nametofont("TkFixedFont").configure(family=mono_family, size=9)
        self._ui_font = (ui_family, 9)
        self._ui_font_bold = (ui_family, 9, "bold")
        self._button_font = (ui_family, 9, "bold")
        self._log_font = (mono_family, 9)

        app_bg = "#f4f7fb"
        panel_bg = "#ffffff"
        border = "#d7dee8"
        text = "#1f2937"
        muted = "#5f6b7a"
        primary = "#0a66c2"
        primary_active = "#0858a8"
        danger = "#b42318"
        danger_active = "#941b12"
        secondary_bg = "#eaf3ff"
        secondary_active = "#dbeafe"
        secondary_fg = "#175f9f"
        self._menu_bg = panel_bg
        self._menu_fg = text
        self._menu_active_bg = "#d7eaff"
        self._menu_active_fg = "#102a43"
        self._menu_disabled_fg = "#8a95a3"

        self.root.configure(background=app_bg)
        style.configure(".", font=self._ui_font, background=app_bg, foreground=text)
        style.configure("TFrame", background=app_bg)
        style.configure("TLabel", background=app_bg, foreground=text)
        style.configure("TCheckbutton", background=app_bg, foreground=text)
        style.configure(
            "Modern.TCheckbutton",
            background=app_bg,
            foreground=text,
            font=self._ui_font,
            padding=(2, 3),
            focuscolor="#bfdbfe",
        )
        style.map(
            "Modern.TCheckbutton",
            background=[
                ("active", app_bg),
                ("selected", app_bg),
                ("disabled", app_bg),
            ],
            foreground=[
                ("disabled", "#8a95a3"),
                ("active", text),
                ("selected", text),
            ],
            indicatorcolor=[
                ("selected", primary),
                ("pressed", primary_active),
                ("active", "#d7eaff"),
                ("disabled", "#d8dee6"),
                ("!selected", "#ffffff"),
            ],
            bordercolor=[
                ("selected", primary),
                ("focus", "#8fc5f5"),
                ("active", "#8fc5f5"),
                ("disabled", "#d8dee6"),
            ],
        )
        style.configure(
            "TEntry",
            fieldbackground=panel_bg,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=(6, 4),
        )
        style.map(
            "TEntry",
            fieldbackground=[("readonly", "#f8fafc"), ("disabled", "#eef2f7")],
            foreground=[("disabled", muted)],
            bordercolor=[("focus", "#8fc5f5")],
        )
        style.configure(
            "TCombobox",
            fieldbackground=panel_bg,
            foreground=text,
            background=panel_bg,
            arrowcolor=muted,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=(5, 3),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", panel_bg), ("disabled", "#eef2f7")],
            foreground=[("disabled", muted)],
            arrowcolor=[("disabled", "#9aa4b2")],
            bordercolor=[("focus", "#8fc5f5")],
        )
        style.configure(
            "StatusEditor.TCombobox",
            fieldbackground=panel_bg,
            foreground=text,
            background=panel_bg,
            arrowcolor=muted,
            bordercolor="#8fc5f5",
            lightcolor="#8fc5f5",
            darkcolor=border,
            padding=(5, 3),
            font=self._ui_font,
        )
        style.map(
            "StatusEditor.TCombobox",
            fieldbackground=[("readonly", panel_bg), ("disabled", "#eef2f7")],
            foreground=[("disabled", muted)],
            arrowcolor=[("disabled", "#9aa4b2")],
            bordercolor=[("focus", "#8fc5f5")],
        )
        style.configure("TButton", padding=(10, 5), background="#f8fafc", foreground=text, bordercolor=border)
        style.map(
            "TButton",
            background=[("active", "#eef2f7"), ("disabled", "#eef2f7")],
            foreground=[("disabled", "#8a95a3")],
        )
        style.configure(
            "TScrollbar",
            background="#e5ebf2",
            troughcolor=app_bg,
            bordercolor=border,
            arrowcolor=muted,
        )
        style.map("TScrollbar", background=[("active", "#d7dee8")], arrowcolor=[("disabled", "#9aa4b2")])
        style.configure(
            "Grouped.TLabelframe",
            background=app_bg,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            relief="solid",
            padding=(10, 8),
        )
        style.configure("Grouped.TLabelframe.Label", background=app_bg, foreground="#334155", font=self._ui_font_bold)
        style.configure(
            "Primary.TButton",
            background=primary,
            foreground="#ffffff",
            bordercolor=primary,
            focuscolor="#bfdbfe",
            padding=(13, 6),
            font=self._button_font,
        )
        style.map(
            "Primary.TButton",
            background=[("active", primary_active), ("pressed", "#074a8f"), ("disabled", "#d8dee6")],
            foreground=[("disabled", "#758195")],
            bordercolor=[("active", primary_active), ("disabled", "#d8dee6")],
        )
        style.configure(
            "SecondaryAccent.TButton",
            background=secondary_bg,
            foreground=secondary_fg,
            bordercolor="#b6d7f7",
            focuscolor="#d7ebff",
            padding=(11, 5),
            font=self._button_font,
        )
        style.map(
            "SecondaryAccent.TButton",
            background=[("active", secondary_active), ("pressed", "#cfe4ff"), ("disabled", "#eef2f7")],
            foreground=[("active", "#0f4f85"), ("disabled", "#7a8796")],
            bordercolor=[("active", "#8fc5f5"), ("disabled", "#d8dee6")],
        )
        style.configure(
            "Danger.TButton",
            background=danger,
            foreground="#ffffff",
            bordercolor=danger,
            focuscolor="#fecaca",
            padding=(11, 5),
            font=self._button_font,
        )
        style.map(
            "Danger.TButton",
            background=[("active", danger_active), ("pressed", "#7f1d1d"), ("disabled", "#eef2f7")],
            foreground=[("disabled", "#8a95a3")],
            bordercolor=[("active", danger_active), ("disabled", "#d8dee6")],
        )
        style.configure("CookieStatus.TLabel", foreground=muted, background=app_bg, font=(ui_family, 8))
        style.configure("ChannelDisplay.TLabel", foreground=muted, background=app_bg, font=(ui_family, 8))
        style.configure(
            "Treeview",
            background=panel_bg,
            fieldbackground=panel_bg,
            foreground=text,
            font=self._ui_font,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            borderwidth=1,
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            background="#eef3f8",
            foreground="#334155",
            font=self._ui_font_bold,
            relief="flat",
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=(8, 6),
        )
        style.map(
            "Treeview",
            background=[("selected", "#d7eaff")],
            foreground=[("selected", "#102a43")],
        )
        style.map("Treeview.Heading", background=[("active", "#e3ebf4")])

    def _build_ui(self) -> None:
        self._configure_ui_styles()

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=(14, 12))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, minsize=380, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(4, weight=1)

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=2)

        source_frame = ttk.LabelFrame(left, text="Nguồn", padding=(12, 10), style="Grouped.TLabelframe")
        source_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        source_frame.columnconfigure(1, weight=1)

        ttk.Label(source_frame, text="YouTube API Key").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.api_key_entry = ttk.Entry(source_frame, textvariable=self.api_key_var)
        self.api_key_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        ttk.Label(source_frame, text="URL kênh / ID kênh / Handle").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=2
        )
        self.channel_entry = ttk.Entry(source_frame, textvariable=self.channel_var)
        self.channel_entry.grid(row=1, column=1, sticky="ew", pady=2)
        self.fetch_button = ttk.Button(
            source_frame,
            text="Lấy danh sách Video",
            command=self.start_fetch,
            style="Primary.TButton",
        )
        self.fetch_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=2)
        self.channel_display_label = ttk.Label(
            source_frame,
            textvariable=self.channel_display_var,
            style="ChannelDisplay.TLabel",
            width=62,
            anchor="w",
        )
        self.channel_display_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 0))

        filter_frame = ttk.LabelFrame(left, text="Bộ lọc", padding=(12, 10), style="Grouped.TLabelframe")
        filter_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        filter_frame.columnconfigure(1, weight=1)
        ttk.Label(filter_frame, text="Bộ lọc").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.filter_box = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=(FILTER_ALL, FILTER_NOT_DOWNLOADED),
            state="readonly",
            width=32,
        )
        self.filter_box.grid(row=0, column=1, sticky="ew", pady=2)
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_filter())
        self._block_combobox_mousewheel(self.filter_box)

        ttk.Label(filter_frame, text="Tìm kiếm tiêu đề").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        search_frame = ttk.Frame(filter_frame)
        search_frame.grid(row=1, column=1, sticky="ew", pady=2)
        search_frame.columnconfigure(0, weight=1)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=24)
        search_entry.grid(row=0, column=0, sticky="ew")
        search_entry.bind("<Return>", lambda _event: self._find_next_match())
        search_entry.bind("<Shift-Return>", lambda _event: self._find_previous_match())
        ttk.Label(search_frame, textvariable=self.search_status_var, width=12).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        duration_validate = (self.root.register(self._validate_duration_filter_text), "%P")

        below_frame = ttk.Frame(filter_frame)
        below_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        self.hide_below_check = ttk.Checkbutton(
            below_frame,
            text="Ẩn video dưới:",
            variable=self.hide_below_enabled_var,
            command=self._on_duration_filter_changed,
            style="Modern.TCheckbutton",
        )
        self.hide_below_check.grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.hide_below_entry = ttk.Entry(
            below_frame,
            textvariable=self.hide_below_minutes_var,
            validate="key",
            validatecommand=duration_validate,
            width=5,
            justify="center",
        )
        self.hide_below_entry.grid(row=0, column=1, sticky="w")
        self.hide_below_entry.bind("<FocusOut>", lambda _event: self._restore_empty_duration_filter_entry("below"))
        ttk.Label(below_frame, text="phút").grid(row=0, column=2, sticky="w", padx=(4, 0))

        above_frame = ttk.Frame(filter_frame)
        above_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        self.hide_above_check = ttk.Checkbutton(
            above_frame,
            text="Ẩn video trên:",
            variable=self.hide_above_enabled_var,
            command=self._on_duration_filter_changed,
            style="Modern.TCheckbutton",
        )
        self.hide_above_check.grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.hide_above_entry = ttk.Entry(
            above_frame,
            textvariable=self.hide_above_minutes_var,
            validate="key",
            validatecommand=duration_validate,
            width=5,
            justify="center",
        )
        self.hide_above_entry.grid(row=0, column=1, sticky="w")
        self.hide_above_entry.bind("<FocusOut>", lambda _event: self._restore_empty_duration_filter_entry("above"))
        ttk.Label(above_frame, text="phút").grid(row=0, column=2, sticky="w", padx=(4, 0))

        table_group = ttk.LabelFrame(right, text="Danh sách video", padding=(12, 10), style="Grouped.TLabelframe")
        table_group.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        table_group.columnconfigure(0, weight=1)
        table_group.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(table_group)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(table_frame, columns=TREE_COLUMN_IDS, show="headings", selectmode="browse")
        self.tree.heading("selected", text="[ ]", anchor="center")
        self.tree.heading("title", text="Tiêu đề video")
        self.tree.heading("duration", text="Thời lượng")
        self.tree.heading("published", text="Ngày đăng")
        self.tree.heading("status", text="Trạng thái")
        for column_id in TREE_COLUMN_IDS:
            self.tree.column(column_id, **TREE_COLUMN_DEFAULTS[column_id])
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<B1-Motion>", self._on_tree_drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_button_release, add="+")
        self.tree.bind("<Configure>", self._on_tree_configure, add="+")
        self._schedule_tree_column_fit()
        self.status_menu = tk.Menu(
            self.root,
            tearoff=0,
            background=getattr(self, "_menu_bg", "#ffffff"),
            foreground=getattr(self, "_menu_fg", "#1f2937"),
            activebackground=getattr(self, "_menu_active_bg", "#d7eaff"),
            activeforeground=getattr(self, "_menu_active_fg", "#102a43"),
            disabledforeground=getattr(self, "_menu_disabled_fg", "#8a95a3"),
            font=getattr(self, "_ui_font", ("Segoe UI", 9)),
            borderwidth=1,
            relief="solid",
        )
        self.status_menu.add_command(
            label="Đánh dấu là Đã tải",
            command=lambda: self._apply_manual_status_to_selected("Đã tải"),
        )
        self.status_menu.add_command(
            label="Đánh dấu là Chưa tải",
            command=lambda: self._apply_manual_status_to_selected("Chưa tải"),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu thumbnail",
            command=lambda: self._apply_manual_status_to_selected("Thiếu thumbnail"),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu video",
            command=lambda: self._apply_manual_status_to_selected("Thiếu video"),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu audio",
            command=lambda: self._apply_manual_status_to_selected(STATUS_MISSING_AUDIO),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu video/audio",
            command=lambda: self._apply_manual_status_to_selected(STATUS_MISSING_VIDEO_AUDIO),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu video/thumbnail",
            command=lambda: self._apply_manual_status_to_selected(STATUS_MISSING_VIDEO_THUMB),
        )
        self.status_menu.add_command(
            label="Đánh dấu thiếu audio/thumbnail",
            command=lambda: self._apply_manual_status_to_selected(STATUS_MISSING_AUDIO_THUMB),
        )
        self.status_menu.add_separator()
        self.status_menu.add_command(
            label="Xoá trạng thái thủ công",
            command=self._clear_manual_status_for_selected,
        )

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)
        table_actions = ttk.Frame(table_frame)
        table_actions.grid(row=1, column=0, sticky="e", pady=(8, 0))
        self.more_button = ttk.Button(table_actions, text="Xem thêm video", command=self.start_load_more)
        self.more_button.grid(row=0, column=0, sticky="e")
        self.select_by_date_button = ttk.Button(
            table_actions,
            text="Chọn video theo ngày",
            command=self.open_select_by_date_dialog,
        )
        self.select_by_date_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        output_frame = ttk.LabelFrame(left, text="Đầu ra & Cookies", padding=(10, 8), style="Grouped.TLabelframe")
        output_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Thư mục lưu").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(output_frame, textvariable=self.save_folder_var, state="readonly").grid(
            row=0, column=1, sticky="ew", pady=2
        )
        self.choose_folder_button = ttk.Button(
            output_frame,
            text="Chọn thư mục",
            command=self.choose_save_folder,
            style="SecondaryAccent.TButton",
        )
        self.choose_folder_button.grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=2)

        self.cookies_check = ttk.Checkbutton(
            output_frame,
            text="Sử dụng Cookies",
            variable=self.cookies_enabled_var,
            command=self._update_cookies_state,
            style="Modern.TCheckbutton",
        )
        self.cookies_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))

        ttk.Label(output_frame, text="Nguồn cookie").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        self.cookie_source_box = ttk.Combobox(
            output_frame,
            textvariable=self.cookie_source_var,
            values=tuple(COOKIE_SOURCE_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.cookie_source_box.grid(row=2, column=1, sticky="w", pady=2)
        self.cookie_source_box.bind("<<ComboboxSelected>>", lambda _event: self._on_cookie_source_changed())
        self._block_combobox_mousewheel(self.cookie_source_box)

        self.cookie_path_label = ttk.Label(output_frame, text="File cookie", width=16)
        self.cookie_path_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        self.cookie_path_entry = ttk.Entry(
            output_frame,
            textvariable=self.cookies_path_var,
            state="disabled",
            width=26,
        )
        self.cookie_path_entry.grid(row=3, column=1, sticky="ew", pady=2)
        self.cookie_path_button = ttk.Button(
            output_frame,
            text="Chọn cookies*.txt",
            command=self.choose_cookies_file,
            width=22,
            style="SecondaryAccent.TButton",
        )
        self.cookie_path_button.grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=2)
        self.cookies_path_label = self.cookie_path_label
        self.cookies_entry = self.cookie_path_entry
        self.cookies_button = self.cookie_path_button
        self.bridge_cookie_path_label = self.cookie_path_label
        self.bridge_cookie_entry = self.cookie_path_entry
        self.bridge_cookie_button = self.cookie_path_button
        self.cookie_status_label = ttk.Label(
            output_frame,
            textvariable=self.cookie_status_var,
            style="CookieStatus.TLabel",
            width=48,
            anchor="w",
        )
        self.cookie_status_label.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(0, 2))

        download_frame = ttk.LabelFrame(left, text="Tải xuống", padding=(12, 10), style="Grouped.TLabelframe")
        download_frame.grid(row=3, column=0, sticky="ew")
        download_frame.columnconfigure(1, weight=1)

        ttk.Label(download_frame, text="Kiểu tải").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.mode_box = ttk.Combobox(
            download_frame,
            textvariable=self.download_mode_var,
            values=DOWNLOAD_MODES,
            state="readonly",
            width=30,
        )
        self.mode_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self._on_download_mode_changed())
        self._block_combobox_mousewheel(self.mode_box)

        ttk.Label(download_frame, text="Giới hạn tốc độ").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        speed_frame = ttk.Frame(download_frame)
        speed_frame.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
        self.speed_limit_entry = ttk.Entry(speed_frame, textvariable=self.speed_limit_var, width=18)
        self.speed_limit_entry.grid(row=0, column=0, sticky="w")
        ttk.Label(speed_frame, text="MB/s").grid(row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(download_frame, text="File start number").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        file_number_validate = (self.root.register(self._validate_file_start_number_input), "%P")
        self.file_start_number_entry = ttk.Entry(
            download_frame,
            textvariable=self.file_start_number_var,
            width=10,
            validate="key",
            validatecommand=file_number_validate,
        )
        self.file_start_number_entry.grid(row=2, column=1, sticky="w", pady=2)

        ttk.Label(download_frame, text="Download engine").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        self.download_engine_box = ttk.Combobox(
            download_frame,
            textvariable=self.download_engine_var,
            values=tuple(DOWNLOAD_ENGINE_LABELS.values()),
            state="readonly",
            width=30,
        )
        self.download_engine_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=2)
        self._block_combobox_mousewheel(self.download_engine_box)

        download_actions = ttk.Frame(download_frame)
        download_actions.grid(row=4, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.download_button = ttk.Button(download_actions, command=self.start_download, style="Primary.TButton")
        self.download_button.grid(row=0, column=0, sticky="e")
        self.stop_button = ttk.Button(
            download_actions,
            text="Dừng tải",
            command=self.stop_download,
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        progress_frame = ttk.LabelFrame(right, text="Tiến trình / Nhật ký", padding=(12, 10), style="Grouped.TLabelframe")
        progress_frame.grid(row=1, column=0, sticky="nsew")
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.rowconfigure(1, weight=1)
        progress_status_frame = ttk.Frame(progress_frame)
        progress_status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        progress_status_frame.columnconfigure(0, weight=1)
        ttk.Label(progress_status_frame, textvariable=self.progress_current_var, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(progress_status_frame, textvariable=self.progress_detail_var, anchor="w").grid(
            row=1, column=0, sticky="ew"
        )
        log_frame = ttk.Frame(progress_frame)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=6,
            wrap="word",
            state="disabled",
            font=self._log_font,
            background="#ffffff",
            foreground="#1f2937",
            insertbackground="#1f2937",
            selectbackground="#d7eaff",
            selectforeground="#102a43",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#d7dee8",
            highlightcolor="#8fc5f5",
            padx=8,
            pady=6,
            spacing1=1,
            spacing3=2,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("success", foreground="green")
        self.log_text.tag_configure("error", foreground="red")
        self.log_text.tag_configure("skip", foreground="#b58900")
        self.log_text.tag_configure("warning", foreground="#b58900")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _fit_initial_window_to_content(self) -> None:
        self.root.update_idletasks()
        requested_height = self.root.winfo_reqheight()
        maximum_width, maximum_height = self.root.maxsize()
        target_width, target_height = _initial_window_size(
            requested_height,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            maximum_width,
            maximum_height,
        )
        minimum_height = min(
            target_height,
            max(MINIMUM_WINDOW_HEIGHT, requested_height),
        )
        self.root.minsize(
            min(MINIMUM_WINDOW_WIDTH, target_width),
            minimum_height,
        )
        self.root.geometry(f"{target_width}x{target_height}")

    def _log_api_key_persistence_startup_status(self) -> None:
        status = getattr(self, "_api_key_persistence_status", "")
        storage_available = bool(getattr(self, "_api_key_storage_available", False))
        if not storage_available:
            self._append_log("[WARNING] Không thể dùng bảo vệ Windows để lưu API key trên máy này.")
        if status == "decrypt_failed":
            self._append_log("[WARNING] Không thể giải mã API key đã lưu; dữ liệu bảo vệ được giữ nguyên.")
        elif status == "unsupported_payload":
            self._append_log("[WARNING] Dữ liệu API key đã lưu không hợp lệ; dữ liệu bảo vệ được giữ nguyên.")
        elif status == "secure_storage_unavailable" and storage_available:
            self._append_log("[WARNING] Không thể dùng kho bảo vệ Windows cho API key đã lưu.")
        elif status == "settings_write_failed":
            self._append_log("[WARNING] Không thể cập nhật cài đặt API key đã lưu.")

    def start_fetch(self) -> None:
        if self.fetching or self.loading_more or self.downloading or self.shutdown_in_progress:
            return

        channel_input = self.channel_var.get().strip()
        if not channel_input:
            friendly = self._friendly_general_message("Cannot resolve channel")
            self._append_log(friendly)
            self._show_error_dialog(friendly)
            return

        manual_key = self.api_key_var.get().strip()
        if not self._validate_duration_filter_inputs():
            return
        self._apply_live_duration_filter_if_valid()
        context = self._capture_channel_request_context()

        self._channel_generation += 1
        request_token = _FetchRequestToken(
            generation=self._channel_generation,
            request_id=self._next_channel_request_id(),
            channel_input=channel_input,
            context=context,
        )
        self._active_fetch_request = request_token
        self._active_fetch_manual_key = manual_key
        self._active_fetch_manual_key_request_id = request_token.request_id
        self.fetching = True
        self._refresh_interaction_control_states()
        self.apply_filter()

        try:
            worker = threading.Thread(
                target=self._fetch_worker,
                args=(request_token, manual_key),
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._active_fetch_request = None
            self.fetching = False
            self._restore_loaded_generation_after_failed_fetch()
            self._clear_pending_fetch_manual_key(request_token)
            self._refresh_interaction_control_states()
            friendly = self._friendly_general_message(str(exc) or "Could not start fetch worker")
            self._append_log(friendly)
            self._show_error_dialog(friendly)

    def _fetch_worker(
        self,
        request_token: _FetchRequestToken,
        manual_key: str,
    ) -> None:
        try:
            context = request_token.context
            log = lambda message: self._queue_channel_request_log(request_token, message)
            channel, videos, next_page_token = fetch_latest_video_page(
                request_token.channel_input,
                manual_key,
                progress=log,
                hide_below_duration_enabled=context.hide_below_enabled,
                min_visible_duration_seconds=context.hide_below_minutes * 60,
                hide_above_duration_enabled=context.hide_above_enabled,
                max_visible_duration_seconds=context.hide_above_minutes * 60,
            )
            log("[INFO] Checking local files...")
            apply_statuses(
                videos,
                context.save_folder,
                channel.channel_name,
                channel.channel_id,
                download_mode=context.download_mode,
                warning_callback=log,
            )
            hidden_by_duration = self._duration_hidden_count(videos, context)
            if hidden_by_duration:
                log(f"[INFO] Đã ẩn {hidden_by_duration} video theo thời lượng.")
            visible_count = len(videos) - hidden_by_duration
            log(f"[SUCCESS] Đã nạp {visible_count} video sau khi lọc thời lượng.")
            if not next_page_token:
                log("[INFO] Không còn video nào.")
            self.events.put(("fetch_done", request_token, channel, videos, next_page_token))
        except YoutubeApiError as exc:
            self.events.put(("fetch_error", request_token, self._friendly_api_message(exc)))
        except Exception as exc:
            self.events.put(("fetch_error", request_token, self._friendly_general_message(str(exc) or "Network error")))

    def _update_channel_input_display(self) -> None:
        self.channel_display_var.set(self._format_channel_display(self.channel_var.get()))

    def _format_channel_display(self, raw_text: str) -> str:
        text = (raw_text or "").strip()
        if not text:
            return "Hiển thị: -"
        decoded = urllib.parse.unquote(text)
        return f"Hiển thị: {self._shorten_middle(decoded, max_length=72)}"

    def _shorten_middle(self, text: str, max_length: int = 90) -> str:
        value = text or ""
        if len(value) <= max_length:
            return value
        if max_length <= 3:
            return "." * max_length
        keep = max_length - 3
        left = keep // 2
        right = keep - left
        return f"{value[:left]}...{value[-right:]}"

    def _set_resolved_channel_display(self, channel) -> None:
        channel_name = (getattr(channel, "channel_name", "") or "-").strip()
        channel_id = (getattr(channel, "channel_id", "") or "-").strip()
        self.channel_display_var.set(self._shorten_middle(f"Kênh: {channel_name} • {channel_id}", max_length=72))

    def start_load_more(self) -> None:
        if self.loading_more or self.fetching or self.downloading or self.shutdown_in_progress:
            return
        if not self.channel_info or not self.next_page_token:
            self._append_log("[INFO] Không còn video nào.")
            self.next_page_token = ""
            self._update_more_button_state()
            return

        if self._loaded_channel_generation is None:
            self._update_more_button_state()
            return

        if not self._validate_duration_filter_inputs():
            return
        self._apply_live_duration_filter_if_valid()

        context = self._loaded_channel_context
        if context is None:
            self._update_more_button_state()
            return

        request_token = _LoadMoreRequestToken(
            generation=self._loaded_channel_generation,
            request_id=self._next_channel_request_id(),
            channel_id=str(self.channel_info.channel_id or ""),
            uploads_playlist_id=str(self.channel_info.uploads_playlist_id or ""),
            page_token=str(self.next_page_token or ""),
            start_order=len(self.videos) + 1,
            context=context,
        )
        self._active_load_more_request = request_token
        self.loading_more = True
        self._refresh_interaction_control_states()
        try:
            worker = threading.Thread(
                target=self._load_more_worker,
                args=(request_token, self.api_key_var.get().strip()),
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._active_load_more_request = None
            self.loading_more = False
            self._refresh_interaction_control_states()
            friendly = self._friendly_general_message(str(exc) or "Could not start load-more worker")
            self._append_log(friendly)
            self._show_error_dialog(friendly)

    def _load_more_worker(
        self,
        request_token: _LoadMoreRequestToken,
        manual_key: str,
    ) -> None:
        try:
            context = request_token.context
            log = lambda message: self._queue_channel_request_log(request_token, message)
            log("[INFO] Đang nạp thêm 100 video tiếp theo...")
            videos, next_page_token = fetch_more_videos(
                request_token.uploads_playlist_id,
                request_token.page_token,
                request_token.start_order,
                manual_key,
                progress=log,
                hide_below_duration_enabled=context.hide_below_enabled,
                min_visible_duration_seconds=context.hide_below_minutes * 60,
                hide_above_duration_enabled=context.hide_above_enabled,
                max_visible_duration_seconds=context.hide_above_minutes * 60,
            )
            hidden_by_duration = self._duration_hidden_count(videos, context)
            if hidden_by_duration:
                log(f"[INFO] Đã ẩn {hidden_by_duration} video theo thời lượng.")
            if videos:
                visible_count = len(videos) - hidden_by_duration
                log(f"[SUCCESS] Đã nạp thêm {visible_count} video sau khi lọc thời lượng.")
            if not next_page_token:
                log("[INFO] Không còn video nào.")
            self.events.put(("load_more_done", request_token, videos, next_page_token))
        except YoutubeApiError as exc:
            self.events.put(("load_more_error", request_token, self._friendly_api_message(exc)))
        except Exception as exc:
            self.events.put(("load_more_error", request_token, self._friendly_general_message(str(exc) or "Network error")))

    def _block_combobox_mousewheel(self, combobox: ttk.Combobox) -> None:
        combobox.bind("<MouseWheel>", self._ignore_combobox_mousewheel, add="+")
        combobox.bind("<Button-4>", self._ignore_combobox_mousewheel, add="+")
        combobox.bind("<Button-5>", self._ignore_combobox_mousewheel, add="+")

    def _ignore_combobox_mousewheel(self, _event=None):
        return "break"

    def choose_save_folder(self) -> None:
        if self._channel_request_busy() or self.downloading or self.shutdown_in_progress:
            return
        folder = filedialog.askdirectory(title="Chọn thư mục lưu")
        if not folder:
            return
        self.save_folder_var.set(folder)
        if self.channel_info and self.videos:
            self._append_log("[INFO] Checking local files...")
            apply_statuses(
                self.videos,
                folder,
                self.channel_info.channel_name,
                self.channel_info.channel_id,
                download_mode=self.download_mode_var.get(),
                warning_callback=self._append_log,
            )
            self.apply_filter()

    def choose_cookies_file(self) -> None:
        if self.downloading:
            return
        path = filedialog.askopenfilename(
            title="Chọn cookies*.txt",
            filetypes=(("Tệp cookies", "cookies*.txt"), ("Tệp văn bản", "*.txt"), ("Tất cả tệp", "*.*")),
        )
        if path:
            self.cookies_path_var.set(path)
            if not save_cookies_path(path):
                self._append_log("[WARNING] Không thể ghi nhớ đường dẫn file cookies.")
            self._refresh_cookie_status()

    def choose_bridge_cookie_file(self) -> None:
        if self.downloading:
            return
        path = filedialog.askopenfilename(
            title="Chọn youtube_cookies.txt",
            filetypes=(("Cookie YouTube", "youtube_cookies.txt"), ("Tệp văn bản", "*.txt"), ("Tất cả tệp", "*.*")),
        )
        if path:
            self.bridge_cookie_path_var.set(path)
            if not save_bridge_cookie_path(path):
                self._append_log("[WARNING] Không thể ghi nhớ đường dẫn Cookie Bridge.")
            self._refresh_cookie_status()

    def _current_cookie_source(self) -> str:
        return COOKIE_SOURCE_VALUES_BY_LABEL.get(self.cookie_source_var.get(), COOKIE_SOURCE_FILE)

    def _current_download_engine(self) -> str:
        return DOWNLOAD_ENGINE_VALUES_BY_LABEL.get(self.download_engine_var.get(), DOWNLOAD_ENGINE_STABLE)

    def _on_cookie_source_changed(self) -> None:
        save_cookie_source(self._current_cookie_source())
        self._update_cookies_state()
        self._refresh_cookie_status()

    def _update_bridge_cookie_status(self) -> None:
        self._refresh_cookie_status()

    def _poll_cookie_status(self) -> None:
        if self._root_destroyed:
            return
        self._refresh_cookie_status()
        self._after(COOKIE_STATUS_POLL_MS, self._poll_cookie_status)

    def _refresh_cookie_status(self) -> None:
        if not self.cookies_enabled_var.get():
            self.cookie_status_var.set("Cookies: tắt")
            return

        if self._current_cookie_source() == COOKIE_SOURCE_BRIDGE:
            self.cookie_status_var.set(
                self._cookie_file_metadata_status("Cookie Bridge", self.bridge_cookie_path_var.get())
            )
            return

        self.cookie_status_var.set(
            self._cookie_file_metadata_status("File cookie", self.cookies_path_var.get())
        )

    def _cookie_file_metadata_status(self, label: str, path_text: str) -> str:
        if not isinstance(path_text, str):
            return f"{label}: không tìm thấy"
        path_text = path_text.strip()
        if not path_text:
            return f"{label}: chưa chọn file"
        if "\x00" in path_text or len(path_text) > 32767:
            return f"{label}: không tìm thấy"

        try:
            path = Path(path_text)
            stat = path.stat()
            if not path.is_file():
                raise OSError
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, OverflowError):
            return f"{label}: không tìm thấy"

        return f"{label}: tìm thấy \u2022 {stat.st_size} byte \u2022 cập nhật {updated}"

    def _on_download_mode_changed(self) -> None:
        if self.downloading:
            return
        if self.channel_info and self.videos:
            apply_statuses(
                self.videos,
                self.save_folder_var.get().strip(),
                self.channel_info.channel_name,
                self.channel_info.channel_id,
                download_mode=self.download_mode_var.get(),
                warning_callback=self._append_log,
            )
            self.apply_filter()

    def _on_duration_filter_changed(self) -> None:
        self._refresh_duration_entry_states()
        self._apply_live_duration_filter_if_valid()

    def _on_duration_filter_text_changed(self, *_args) -> None:
        self._apply_live_duration_filter_if_valid()

    def _validate_file_start_number_input(self, proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    def open_select_by_date_dialog(self) -> None:
        if self.downloading:
            return
        if not self.videos:
            self._append_log("[ERROR] Chưa có video nào trong danh sách.")
            self._show_error_dialog("Chưa có video nào trong danh sách.", title="Chưa có video")
            return

        from_var = tk.StringVar()
        to_var = tk.StringVar()
        not_downloaded_only_var = tk.BooleanVar(value=False)

        def build_content(context: DialogContext) -> None:
            body = context.body
            body.columnconfigure(1, weight=1)
            ttk.Label(body, text="Từ ngày: YYYY-MM-DD").grid(row=0, column=0, sticky="w", pady=(0, 6))
            from_entry = ttk.Entry(body, textvariable=from_var, width=18)
            from_entry.grid(row=0, column=1, sticky="ew", pady=(0, 6), padx=(8, 0))
            ttk.Label(body, text="Đến ngày: YYYY-MM-DD").grid(row=1, column=0, sticky="w", pady=(0, 6))
            ttk.Entry(body, textvariable=to_var, width=18).grid(row=1, column=1, sticky="ew", pady=(0, 6), padx=(8, 0))
            ttk.Checkbutton(
                body,
                text="Chỉ chọn video chưa tải",
                variable=not_downloaded_only_var,
                style="Modern.TCheckbutton",
            ).grid(row=2, column=0, columnspan=2, sticky="w")
            context.initial_focus = from_entry

        show_app_dialog(
            self.root,
            title="Chọn video theo ngày",
            buttons=(
                {"text": "Hủy", "value": False},
                {
                    "text": "Áp dụng",
                    "command": lambda context: self._apply_date_selection_dialog(
                        context,
                        from_var.get(),
                        to_var.get(),
                        not_downloaded_only_var.get(),
                    ),
                },
            ),
            default_button="Áp dụng",
            cancel_button="Hủy",
            content_builder=build_content,
        )

    def _apply_date_selection_dialog(
        self,
        context: DialogContext,
        from_text: str,
        to_text: str,
        not_downloaded_only: bool,
    ) -> None:
        try:
            from_date = self._parse_dialog_date(from_text, "Từ ngày")
            to_date = self._parse_dialog_date(to_text, "Đến ngày")
        except ValueError as exc:
            self._show_error_dialog(str(exc))
            return

        if from_date > to_date:
            self._show_error_dialog("Từ ngày không được sau Đến ngày.")
            return

        self._append_log("[INFO] Đang chọn video theo ngày đăng...")
        matched_orders: set[int] = set()
        for video in self.videos:
            if not self._video_allowed_by_duration_filter(video):
                continue
            upload_date = self._upload_date_for_video(video)
            if upload_date is None:
                continue
            if not (from_date <= upload_date <= to_date):
                continue
            if not_downloaded_only and not should_show_not_downloaded(video):
                continue
            matched_orders.add(video.display_order)

        self.selected_orders = matched_orders
        self.apply_filter()
        if matched_orders:
            self._append_log(
                f"[SUCCESS] Đã chọn {len(matched_orders)} video từ {from_date.isoformat()} đến {to_date.isoformat()}."
            )
            context.close(True)
        else:
            self._append_log("[WARNING] Không có video nào khớp với khoảng ngày đã chọn.")

    def _parse_dialog_date(self, value: str, label: str) -> date:
        text = (value or "").strip()
        if not text:
            raise ValueError(f"{label} không được để trống.")
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{label} phải có định dạng YYYY-MM-DD.")

    def _upload_date_for_video(self, video) -> date | None:
        try:
            return date.fromisoformat((getattr(video, "published_at", "") or "").strip()[:10])
        except ValueError:
            return None

    def _update_cookies_state(self) -> None:
        source = self._current_cookie_source()
        cookies_enabled = self.cookies_enabled_var.get()

        if self.downloading or not cookies_enabled:
            source_state = "disabled"
            path_entry_state = "disabled"
            path_button_state = "disabled"
        else:
            source_state = "readonly"
            path_entry_state = "readonly"
            path_button_state = "normal"

        self.cookie_source_box.configure(state=source_state)
        self._configure_cookie_path_row(source, path_entry_state, path_button_state)
        self._refresh_cookie_status()

    def _configure_cookie_path_row(self, source: str, entry_state: str, button_state: str) -> None:
        if source == COOKIE_SOURCE_BRIDGE:
            self.cookie_path_entry.configure(textvariable=self.bridge_cookie_path_var)
            self.cookie_path_button.configure(
                text="Chọn youtube_cookies.txt",
                command=self.choose_bridge_cookie_file,
                state=button_state,
            )
            self.cookie_path_entry.configure(state=entry_state)
            return

        self.cookie_path_entry.configure(textvariable=self.cookies_path_var)
        self.cookie_path_button.configure(
            text="Chọn cookies*.txt",
            command=self.choose_cookies_file,
            state=button_state,
        )
        self.cookie_path_entry.configure(state=entry_state)

    def start_download(self) -> None:
        if self._channel_request_busy():
            self._append_log("[INFO] Hãy chờ quá trình nạp danh sách video hoàn tất trước khi tải.")
            return
        if self.downloading or self.shutdown_in_progress:
            return
        if self.download_worker is not None and self.download_worker.is_alive():
            self._append_log("[WARNING] Tiến trình tải trước đó vẫn đang kết thúc.")
            return
        if not self.channel_info or not self.videos:
            self._append_log(self._friendly_general_message("No videos loaded"))
            return

        selected_video_ids = [video.video_id for video in self._selected_visible_videos()]
        selected = self._videos_for_snapshot_ids(selected_video_ids)
        if not selected:
            self._append_log(self._friendly_general_message("No selected videos"))
            return

        try:
            speed_limit = validate_speed_limit(self.speed_limit_var.get())
        except ValueError:
            friendly = self._friendly_general_message("Invalid speed limit")
            self._append_log(friendly)
            self._show_error_dialog(friendly)
            return

        try:
            file_start_number = validate_file_start_number(self.file_start_number_var.get())
        except DownloadError as exc:
            self._show_error_dialog(str(exc), title="Starting file number required")
            self.file_start_number_entry.focus_set()
            return

        options = DownloadOptions(
            base_folder=self.save_folder_var.get().strip(),
            channel_id=self.channel_info.channel_id,
            channel_name=self.channel_info.channel_name,
            cookies_enabled=self.cookies_enabled_var.get(),
            cookies_path=self.cookies_path_var.get().strip(),
            speed_limit=speed_limit,
            download_mode=self.download_mode_var.get(),
            cookie_source=self._current_cookie_source(),
            bridge_cookie_path=self.bridge_cookie_path_var.get().strip(),
            download_engine=self._current_download_engine(),
            file_start_number=file_start_number,
        )
        if not save_cookie_preferences(
            options.cookie_source,
            options.cookies_path,
            options.bridge_cookie_path,
        ):
            self._append_log("[WARNING] Không thể lưu tùy chọn đường dẫn cookies; phiên hiện tại vẫn tiếp tục.")

        try:
            validate_download_environment(options)
        except DownloadError as exc:
            friendly = self._friendly_general_message(str(exc))
            self._append_log(friendly)
            self._show_error_dialog(friendly)
            return

        initial_complete_ids = self._initial_complete_video_ids(
            selected,
            options.channel_id,
            options.download_mode,
        )
        download_run_id = self._begin_download_run_numbering(
            file_start_number,
            selected_video_ids,
            initial_complete_ids,
        )
        self.downloading = True
        self.download_stop_requested = False
        self.exit_after_download_stop = False
        self.close_requested = False
        self.cancel_download = False
        self.shutdown_in_progress = False
        self.shutdown_started_at = None
        self._shutdown_slow_warning_logged = False
        self._shutdown_cancel_reissued = False
        self._download_terminal_received = False
        self._download_terminal_outcome = ""
        self._download_terminal_message = ""
        self.download_controller = DownloadController(
            systemic_block_callback=lambda context: self.events.put(("systemic_download_block", context))
        )
        self._clear_progress_queue()
        self._reset_progress_sticky(reset_order=True)
        self.progress_current_var.set("Đang tải: Sẵn sàng")
        self.progress_detail_var.set("Đang xử lý: -")
        self._set_download_controls_locked(True)
        self.download_worker = threading.Thread(
            target=self._download_worker,
            args=(selected, options, self.download_controller, download_run_id),
            daemon=False,
        )
        self.download_worker.start()

    def _initial_complete_video_ids(
        self,
        selected: list,
        channel_id: str,
        download_mode: str,
    ) -> set[str]:
        complete_ids: set[str] = set()
        for video in selected:
            video_id = str(getattr(video, "video_id", "") or "").strip()
            if video_id and is_mode_complete(get_video_entry(channel_id, video_id), download_mode):
                complete_ids.add(video_id)
        return complete_ids

    def _begin_download_run_numbering(
        self,
        run_start_number: int,
        selected_video_ids,
        initial_complete_ids,
    ) -> int:
        self._download_run_sequence = getattr(self, "_download_run_sequence", 0) + 1
        run_id = self._download_run_sequence
        selected_ids: set[str] = set()
        for video_id in selected_video_ids:
            normalized_id = str(video_id or "").strip()
            if normalized_id:
                selected_ids.add(normalized_id)
        self._active_download_run_id = run_id
        self._download_run_start_number = run_start_number
        self._download_run_selected_ids = selected_ids
        self._download_run_initial_complete_ids = set()
        for video_id in initial_complete_ids:
            normalized_id = str(video_id or "").strip()
            if normalized_id in selected_ids:
                self._download_run_initial_complete_ids.add(normalized_id)
        self._download_run_completed_ids = set()
        return run_id

    def stop_download(self) -> None:
        if not self.downloading:
            return
        self._request_download_stop(
            exit_after=False,
            log_message="[WARNING] Người dùng đã dừng tải.",
        )

    def _request_download_stop(
        self,
        exit_after: bool,
        log_message: str,
        info_message: str = "[INFO] Đang dừng tiến trình tải...",
        emit_stop_progress: bool = True,
    ) -> None:
        if not self.downloading:
            return

        was_close_requested = self.close_requested
        first_stop_request = not self.download_stop_requested
        if exit_after:
            self.exit_after_download_stop = True
            self.close_requested = True
        elif not self.close_requested:
            self.exit_after_download_stop = False

        self.cancel_download = True
        self.download_stop_requested = True
        if first_stop_request or (exit_after and not was_close_requested):
            self._append_log(log_message)
            if emit_stop_progress:
                self._enqueue_progress_event(ProgressEvent(kind="stop_requested"))
            if info_message:
                self._append_log(info_message)

        self._update_stop_button_state()
        if self.download_controller is not None:
            self.download_controller.request_cancel()

    def _download_worker(
        self,
        selected,
        options: DownloadOptions,
        controller: DownloadController,
        download_run_id: int,
    ) -> None:
        outcome = "completed"
        message = ""
        try:
            download_items(
                selected,
                options,
                self._thread_log,
                lambda video: self._queue_download_status(video, download_run_id),
                cancel_controller=controller,
                progress_callback=self._enqueue_progress_event,
            )
        except DownloadError as exc:
            outcome = "error"
            message = self._friendly_general_message(str(exc))
            self._enqueue_progress_event(ProgressEvent(kind="error", phase="Lỗi", message=message))
        except Exception as exc:
            outcome = "error"
            message = self._friendly_general_message(str(exc))
            self._enqueue_progress_event(ProgressEvent(kind="error", phase="Lỗi", message=message))
        finally:
            self.events.put(("download_worker_finished", outcome, message))

    def _queue_download_status(self, video, download_run_id: int) -> None:
        self.events.put(("status_update", video.display_order, video.status))
        if video.status != STATUS_DOWNLOADED:
            return
        try:
            video_id = str(getattr(video, "video_id", "") or "").strip()
        except Exception:
            return
        if video_id:
            self.events.put(
                (
                    "download_video_completed_for_numbering",
                    download_run_id,
                    video_id,
                )
            )

    def apply_filter(self) -> None:
        self._destroy_status_editor()
        self._prune_selected_orders()
        selected_row = self.tree.focus()
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.visible_orders = []
        for video in self.videos:
            if not self._video_allowed_by_duration_filter(video):
                continue
            if self.filter_var.get() == FILTER_NOT_DOWNLOADED and not should_show_not_downloaded(video):
                continue
            order = video.display_order
            self.visible_orders.append(order)
            checked = "[x]" if order in self.selected_orders else "[ ]"
            self.tree.insert(
                "",
                "end",
                iid=str(order),
                values=(checked, video.title, video.duration, video.published_at, video.status),
            )
        if selected_row and self.tree.exists(selected_row):
            self.tree.focus(selected_row)
            self.tree.selection_set(selected_row)
        self._update_header_checkbox()
        self._update_download_button_text()
        self._refresh_search_matches(keep_current=True)
        self._schedule_tree_column_fit()

    def _default_tree_column_ratios(self) -> dict[str, float]:
        data_total = sum(int(TREE_COLUMN_DEFAULTS[column]["width"]) for column in TREE_DATA_COLUMNS)
        if data_total <= 0:
            equal_ratio = 1 / len(TREE_DATA_COLUMNS)
            return {column: equal_ratio for column in TREE_DATA_COLUMNS}
        return {
            column: int(TREE_COLUMN_DEFAULTS[column]["width"]) / data_total
            for column in TREE_DATA_COLUMNS
        }

    def _on_tree_configure(self, _event=None):
        if self.tree_column_drag:
            return None
        self._schedule_tree_column_fit()
        return None

    def _cancel_tree_column_fit(self) -> None:
        if self.tree_column_fit_after_id is None:
            return
        try:
            self.root.after_cancel(self.tree_column_fit_after_id)
        except tk.TclError:
            pass
        self.tree_column_fit_after_id = None

    def _schedule_tree_column_fit(self) -> None:
        if self.tree_column_drag:
            return
        self._cancel_tree_column_fit()
        self.tree_column_fit_after_id = self.root.after_idle(self._fit_tree_columns_to_table)

    def _tree_available_data_width(self) -> int:
        tree_width = int(self.tree.winfo_width() or 0)
        if tree_width <= 1:
            tree_width = sum(int(TREE_COLUMN_DEFAULTS[column]["width"]) for column in TREE_COLUMN_IDS)

        fixed_width = 0
        for column in TREE_FIXED_COLUMNS:
            try:
                fixed_width += int(self.tree.column(column, "width") or TREE_COLUMN_DEFAULTS[column]["width"])
            except (tk.TclError, ValueError):
                fixed_width += int(TREE_COLUMN_DEFAULTS[column]["width"])

        return max(0, tree_width - fixed_width - 4)

    def _tree_data_minwidths(self) -> dict[str, int]:
        return {column: int(TREE_COLUMN_DEFAULTS[column]["minwidth"]) for column in TREE_DATA_COLUMNS}

    def _remember_tree_column_ratios_from_current_widths(self) -> None:
        widths = {}
        for column in TREE_DATA_COLUMNS:
            try:
                width = int(self.tree.column(column, "width"))
            except (tk.TclError, ValueError):
                width = int(TREE_COLUMN_DEFAULTS[column]["width"])
            minwidth = int(TREE_COLUMN_DEFAULTS[column]["minwidth"])
            widths[column] = max(minwidth, width)

        fitted = self._fit_widths_to_available_space(widths, self._tree_available_data_width())
        total = sum(fitted.values())
        if total > 0:
            self.tree_column_ratios = {column: fitted[column] / total for column in TREE_DATA_COLUMNS}

    def _fit_widths_to_available_space(self, desired_widths: dict[str, int], available_width: int) -> dict[str, int]:
        minwidths = self._tree_data_minwidths()
        min_total = sum(minwidths.values())
        if available_width <= min_total:
            return dict(minwidths)

        extra_space = available_width - min_total
        desired_extra = {
            column: max(0, int(desired_widths.get(column, minwidths[column])) - minwidths[column])
            for column in TREE_DATA_COLUMNS
        }
        total_desired_extra = sum(desired_extra.values())
        if total_desired_extra <= 0:
            ratios = self.tree_column_ratios or self._default_tree_column_ratios()
            weights = {column: max(0.0, float(ratios.get(column, 0.0))) for column in TREE_DATA_COLUMNS}
        else:
            weights = desired_extra

        total_weight = sum(weights.values())
        if total_weight <= 0:
            weights = {column: 1 for column in TREE_DATA_COLUMNS}
            total_weight = len(TREE_DATA_COLUMNS)

        fitted = {}
        for column in TREE_DATA_COLUMNS:
            extra = int(extra_space * weights[column] / total_weight)
            fitted[column] = minwidths[column] + extra

        remainder = available_width - sum(fitted.values())
        if remainder > 0:
            fitted["title"] += remainder

        while sum(fitted.values()) > available_width:
            shrinkable = [
                column for column in TREE_DATA_COLUMNS
                if fitted[column] > minwidths[column]
            ]
            if not shrinkable:
                break
            column = max(shrinkable, key=lambda item: fitted[item])
            fitted[column] -= 1

        return {column: max(minwidths[column], fitted[column]) for column in TREE_DATA_COLUMNS}

    def _tree_separator_at_x(self, x: int) -> tuple[str, tuple[str, ...]] | None:
        boundary = 0
        hit_width = 6
        for index, column in enumerate(TREE_COLUMN_IDS):
            try:
                boundary += int(self.tree.column(column, "width"))
            except (tk.TclError, ValueError):
                boundary += int(TREE_COLUMN_DEFAULTS[column]["width"])

            if abs(x - boundary) > hit_width:
                continue

            left_column = column
            if left_column in TREE_FIXED_COLUMNS:
                return None

            right_columns = tuple(
                candidate
                for candidate in TREE_COLUMN_IDS[index + 1:]
                if candidate in TREE_DATA_COLUMNS
            )
            if not right_columns:
                return None
            return left_column, right_columns

        return None

    def _begin_tree_column_resize(self, event):
        separator = self._tree_separator_at_x(event.x)
        if separator is None:
            return None

        left_column, right_columns = separator
        self._cancel_tree_column_fit()
        self._destroy_status_editor()

        start_widths = {}
        for column in TREE_COLUMN_IDS:
            try:
                start_widths[column] = int(self.tree.column(column, "width"))
            except (tk.TclError, ValueError):
                start_widths[column] = int(TREE_COLUMN_DEFAULTS[column]["width"])

        self.tree_column_drag = {
            "start_x": event.x,
            "left_column": left_column,
            "right_columns": right_columns,
            "start_widths": start_widths,
            "available_width": self._tree_available_data_width(),
        }

        return "break"

    def _on_tree_drag_motion(self, event):
        if not self.tree_column_drag:
            return None
        self._apply_tree_column_drag(event.x)
        return "break"

    def _apply_tree_column_drag(self, current_x: int) -> None:
        drag = self.tree_column_drag
        if not drag:
            return

        start_widths = drag["start_widths"]
        left_column = drag["left_column"]
        right_columns = drag["right_columns"]
        delta = current_x - int(drag["start_x"])

        left_start = int(start_widths[left_column])
        left_min = int(TREE_COLUMN_DEFAULTS[left_column]["minwidth"])
        left_shrink_capacity = max(0, left_start - left_min)
        right_shrink_capacity = sum(
            max(0, int(start_widths[column]) - int(TREE_COLUMN_DEFAULTS[column]["minwidth"]))
            for column in right_columns
        )
        delta = max(-left_shrink_capacity, min(delta, right_shrink_capacity))

        new_left_width = left_start + delta
        right_total_start = sum(int(start_widths[column]) for column in right_columns)
        new_right_total = right_total_start - delta
        right_widths = self._distribute_columns_to_total(right_columns, start_widths, new_right_total)

        selected_defaults = TREE_COLUMN_DEFAULTS["selected"]
        self.tree.column(
            "selected",
            width=selected_defaults["width"],
            minwidth=selected_defaults["minwidth"],
            stretch=False,
            anchor=selected_defaults.get("anchor", "w"),
        )
        self.tree.column(left_column, width=new_left_width, stretch=False)
        for column, width in right_widths.items():
            self.tree.column(column, width=width, stretch=False)

    def _distribute_columns_to_total(
        self,
        columns: tuple[str, ...],
        start_widths: dict[str, int],
        target_total: int,
    ) -> dict[str, int]:
        minwidths = {column: int(TREE_COLUMN_DEFAULTS[column]["minwidth"]) for column in columns}
        min_total = sum(minwidths.values())
        if target_total <= min_total:
            return dict(minwidths)

        extra_total = target_total - min_total
        start_extras = {
            column: max(0, int(start_widths.get(column, minwidths[column])) - minwidths[column])
            for column in columns
        }
        total_extra = sum(start_extras.values())
        if total_extra <= 0:
            weights = {column: 1 for column in columns}
            total_weight = len(columns)
        else:
            weights = start_extras
            total_weight = total_extra

        widths = {}
        for column in columns:
            extra = int(extra_total * weights[column] / total_weight)
            widths[column] = minwidths[column] + extra

        remainder = target_total - sum(widths.values())
        if remainder > 0:
            target_column = max(columns, key=lambda item: int(start_widths.get(item, 0)))
            widths[target_column] += remainder

        while sum(widths.values()) > target_total:
            shrinkable = [column for column in columns if widths[column] > minwidths[column]]
            if not shrinkable:
                break
            target_column = max(shrinkable, key=lambda item: widths[item])
            widths[target_column] -= 1

        return {column: max(minwidths[column], widths[column]) for column in columns}

    def _fit_tree_columns_to_table(self) -> None:
        if self.tree_column_drag:
            return
        if self.tree_column_fit_in_progress:
            return

        self.tree_column_fit_after_id = None
        self.tree_column_fit_in_progress = True

        try:
            for column in TREE_FIXED_COLUMNS:
                defaults = TREE_COLUMN_DEFAULTS[column]
                self.tree.column(
                    column,
                    width=defaults["width"],
                    minwidth=defaults["minwidth"],
                    stretch=False,
                    anchor=defaults.get("anchor", "w"),
                )

            available = self._tree_available_data_width()
            minwidths = self._tree_data_minwidths()
            if not self.tree_column_ratios:
                self.tree_column_ratios = self._default_tree_column_ratios()

            desired_widths = {}
            for column in TREE_DATA_COLUMNS:
                ratio = max(0.0, float(self.tree_column_ratios.get(column, 0.0)))
                desired_widths[column] = max(minwidths[column], int(round(available * ratio)))

            fitted_widths = self._fit_widths_to_available_space(desired_widths, available)

            for column in TREE_DATA_COLUMNS:
                defaults = TREE_COLUMN_DEFAULTS[column]
                self.tree.column(
                    column,
                    width=fitted_widths[column],
                    minwidth=defaults["minwidth"],
                    stretch=False,
                    anchor=defaults.get("anchor", "w"),
                )
        finally:
            self.tree_column_fit_in_progress = False

    def _on_search_text_changed(self) -> None:
        if not self.search_var.get().strip():
            self.search_match_orders = []
            self.current_search_match_index = -1
            self.search_status_var.set("")
            return
        self._refresh_search_matches(keep_current=True)

    def _refresh_search_matches(self, keep_current: bool = False) -> None:
        query = self.search_var.get().strip().casefold()
        if not query:
            self.search_match_orders = []
            self.current_search_match_index = -1
            self.search_status_var.set("")
            return

        focused_order = self._focused_order()
        self.search_match_orders = []
        for order in self.visible_orders:
            video = self._video_for_order(order)
            if video and query in getattr(video, "title", "").casefold():
                self.search_match_orders.append(order)

        if not self.search_match_orders:
            self.current_search_match_index = -1
            self.search_status_var.set("Không tìm thấy")
            return

        if keep_current and focused_order in self.search_match_orders:
            self.current_search_match_index = self.search_match_orders.index(focused_order)
            self._update_search_status()
            return

        self.current_search_match_index = -1
        self.search_status_var.set(f"0/{len(self.search_match_orders)}")

    def _find_next_match(self):
        self._move_to_search_match(1)
        return "break"

    def _find_previous_match(self):
        self._move_to_search_match(-1)
        return "break"

    def _move_to_search_match(self, direction: int) -> None:
        query = self.search_var.get().strip()
        if not query:
            self.search_match_orders = []
            self.current_search_match_index = -1
            self.search_status_var.set("")
            return

        self._refresh_search_matches(keep_current=True)
        if not self.search_match_orders:
            return

        if self.current_search_match_index < 0:
            next_index = 0 if direction >= 0 else len(self.search_match_orders) - 1
        else:
            next_index = (self.current_search_match_index + direction) % len(self.search_match_orders)

        self.current_search_match_index = next_index
        self._focus_search_order(self.search_match_orders[next_index])
        self._update_search_status()

    def _focus_search_order(self, order: int) -> None:
        row_id = str(order)
        if not self.tree.exists(row_id):
            return
        self._destroy_status_editor()
        self.tree.focus(row_id)
        self.tree.selection_set(row_id)
        self.tree.see(row_id)

    def _focused_order(self) -> int | None:
        row_id = self.tree.focus()
        if not row_id:
            return None
        try:
            return int(row_id)
        except ValueError:
            return None

    def _update_search_status(self) -> None:
        if not self.search_match_orders:
            self.search_status_var.set("Không tìm thấy" if self.search_var.get().strip() else "")
            return
        if self.current_search_match_index < 0:
            self.search_status_var.set(f"0/{len(self.search_match_orders)}")
            return
        self.search_status_var.set(f"{self.current_search_match_index + 1}/{len(self.search_match_orders)}")

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)

        if region == "separator":
            return self._begin_tree_column_resize(event) or "break"

        drag_result = self._begin_tree_column_resize(event)
        if drag_result == "break":
            return drag_result

        self._destroy_status_editor()

        if region == "heading" and column == "#1":
            if self.downloading:
                return "break"
            self.toggle_visible_selection()
            return "break"

        if region == "cell" and column == "#1":
            if self.downloading:
                return "break"
            row_id = self.tree.identify_row(event.y)
            if not row_id:
                return "break"
            self._toggle_order(int(row_id))
            return "break"

        return None

    def _on_tree_button_release(self, _event=None):
        if not self.tree_column_drag:
            return None
        self._finish_tree_column_drag()
        return "break"

    def _finish_tree_column_drag(self) -> None:
        if not self.tree_column_drag:
            return
        self.tree_column_drag = None
        self._remember_tree_column_ratios_from_current_widths()
        self._schedule_tree_column_fit()

    def _on_tree_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        if region != "cell":
            return None

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return "break"

        video = self._video_for_order(int(row_id))
        if not video:
            return "break"

        if column == "#2":
            self._show_title_copy_popup(getattr(video, "title", ""))
            return "break"

        if column != "#5":
            return None

        if self.downloading:
            return "break"

        self._open_status_editor(row_id, video)
        return "break"

    def _on_tree_right_click(self, event):
        if self.downloading:
            return "break"

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return None

        order = int(row_id)
        if order not in self.selected_orders:
            self.selected_orders.clear()
            self.selected_orders.add(order)
            self.apply_filter()

        self.tree.focus(row_id)
        try:
            self.status_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.status_menu.grab_release()
        return "break"

    def _open_status_editor(self, row_id: str, video) -> None:
        if self.downloading:
            return

        self._destroy_status_editor()
        bbox = self.tree.bbox(row_id, "#5")
        if not bbox:
            return

        x, y, width, height = bbox
        editor = ttk.Combobox(
            self.tree,
            values=SUPPORTED_STATUS_VALUES,
            state="readonly",
            font=self._ui_font,
            style="StatusEditor.TCombobox",
        )
        self._block_combobox_mousewheel(editor)
        current_status = video.status if video.status in SUPPORTED_STATUS_VALUES else SUPPORTED_STATUS_VALUES[0]
        editor.set(current_status)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self.status_editor = editor
        committed = {"done": False}

        def commit(_event=None):
            if committed["done"]:
                return "break"
            committed["done"] = True
            status = editor.get()
            self._destroy_status_editor()
            self._save_manual_status(video, status)
            return "break"

        def cancel(_event=None):
            if not committed["done"]:
                self._destroy_status_editor()
            return "break"

        editor.bind("<<ComboboxSelected>>", commit)
        editor.bind("<Return>", commit)
        editor.bind("<Escape>", cancel)

    def _show_title_copy_popup(self, title: str) -> None:
        show_copy_text_dialog(self.root, "Sao chép tiêu đề video", title)

    def _on_tree_space(self, _event):
        if self.downloading:
            return "break"
        focused = self.tree.focus()
        if focused:
            self._toggle_order(int(focused))
            return "break"
        return None

    def _destroy_status_editor(self) -> None:
        if self.status_editor is not None:
            self.status_editor.destroy()
            self.status_editor = None

    def _toggle_order(self, order: int) -> None:
        if self.downloading:
            return
        if order in self.selected_orders:
            self.selected_orders.remove(order)
        else:
            self.selected_orders.add(order)
        self.apply_filter()

    def toggle_visible_selection(self) -> None:
        if self.downloading:
            return
        visible = set(self.visible_orders)
        if not visible:
            self._update_header_checkbox()
            return
        if visible.issubset(self.selected_orders):
            self.selected_orders.difference_update(visible)
        else:
            self.selected_orders.update(visible)
        self.apply_filter()

    def _prune_selected_orders(self) -> None:
        loaded_orders = {video.display_order for video in self.videos}
        self.selected_orders.intersection_update(loaded_orders)

    def _video_allowed_by_duration_filter(self, video, context: _ChannelRequestContext | None = None) -> bool:
        effective_context = context if context is not None else self._loaded_channel_context
        if effective_context is None:
            return True
        return is_video_visible_by_duration(
            video,
            hide_below_enabled=effective_context.hide_below_enabled,
            min_duration_seconds=effective_context.hide_below_minutes * 60,
            hide_above_enabled=effective_context.hide_above_enabled,
            max_duration_seconds=effective_context.hide_above_minutes * 60,
        )

    def _duration_hidden_count(self, videos: list, context: _ChannelRequestContext) -> int:
        return sum(
            1
            for video in videos
            if not is_video_visible_by_duration(
                video,
                hide_below_enabled=context.hide_below_enabled,
                min_duration_seconds=context.hide_below_minutes * 60,
                hide_above_enabled=context.hide_above_enabled,
                max_duration_seconds=context.hide_above_minutes * 60,
            )
        )

    def _validate_duration_filter_text(self, proposed: str) -> bool:
        try:
            return (
                proposed == ""
                or (
                    len(proposed) <= DURATION_FILTER_MAX_DIGITS
                    and proposed.isascii()
                    and proposed.isdecimal()
                )
            )
        except Exception:
            return False

    def _restore_empty_duration_filter_entry(self, which: str) -> None:
        if which == "below" and self.hide_below_minutes_var.get() == "":
            self.hide_below_minutes_var.set(str(DEFAULT_HIDE_BELOW_MINUTES))
        elif which == "above" and self.hide_above_minutes_var.get() == "":
            self.hide_above_minutes_var.set(str(DEFAULT_HIDE_ABOVE_MINUTES))

    def _normalize_empty_duration_filter_entries(self) -> None:
        self._restore_empty_duration_filter_entry("below")
        self._restore_empty_duration_filter_entry("above")

    def _parse_duration_filter_minutes(self, which: str) -> int | None:
        default = DEFAULT_HIDE_BELOW_MINUTES if which == "below" else DEFAULT_HIDE_ABOVE_MINUTES
        var = self.hide_below_minutes_var if which == "below" else self.hide_above_minutes_var
        raw_text = var.get()
        if raw_text == "":
            return default
        if (
            not isinstance(raw_text, str)
            or not 1 <= len(raw_text) <= DURATION_FILTER_MAX_DIGITS
            or not raw_text.isascii()
            or not raw_text.isdecimal()
        ):
            return None
        value = int(raw_text)
        if not self._duration_filter_value_in_range(value):
            return None
        return value

    def _duration_filter_minutes_or_default(self, which: str) -> int:
        parsed = self._parse_duration_filter_minutes(which)
        if parsed is not None:
            return parsed
        return DEFAULT_HIDE_BELOW_MINUTES if which == "below" else DEFAULT_HIDE_ABOVE_MINUTES

    def _duration_filter_minutes(self, which: str) -> int:
        parser = getattr(self, "_parse_duration_filter_minutes", None)
        if callable(parser):
            parsed = parser(which)
            if parsed is not None:
                return parsed
            return DEFAULT_HIDE_BELOW_MINUTES if which == "below" else DEFAULT_HIDE_ABOVE_MINUTES

        default = DEFAULT_HIDE_BELOW_MINUTES if which == "below" else DEFAULT_HIDE_ABOVE_MINUTES
        var = self.hide_below_minutes_var if which == "below" else self.hide_above_minutes_var
        raw_text = var.get()
        if raw_text == "":
            return default
        if (
            not isinstance(raw_text, str)
            or not 1 <= len(raw_text) <= DURATION_FILTER_MAX_DIGITS
            or not raw_text.isascii()
            or not raw_text.isdecimal()
        ):
            return default
        value = int(raw_text)
        if not DURATION_FILTER_MIN_MINUTES <= value <= DURATION_FILTER_MAX_MINUTES:
            return default
        return value

    def _current_duration_filter_values(self) -> tuple[bool, int, bool, int] | None:
        below_enabled = bool(self.hide_below_enabled_var.get())
        above_enabled = bool(self.hide_above_enabled_var.get())
        below_minutes = self._parse_duration_filter_minutes("below")
        above_minutes = self._parse_duration_filter_minutes("above")

        if below_enabled and below_minutes is None:
            return None
        if above_enabled and above_minutes is None:
            return None

        if below_minutes is None:
            below_minutes = DEFAULT_HIDE_BELOW_MINUTES
        if above_minutes is None:
            above_minutes = DEFAULT_HIDE_ABOVE_MINUTES

        if below_enabled and above_enabled and above_minutes <= below_minutes:
            return None

        return below_enabled, below_minutes, above_enabled, above_minutes

    def _apply_live_duration_filter_if_valid(self) -> bool:
        if self.fetching or self.loading_more or self.downloading or self.shutdown_in_progress:
            return False

        values = self._current_duration_filter_values()
        if values is None:
            return False

        context = self._loaded_channel_context
        if context is None:
            return True

        below_enabled, below_minutes, above_enabled, above_minutes = values
        updated_context = replace(
            context,
            hide_below_enabled=below_enabled,
            hide_below_minutes=below_minutes,
            hide_above_enabled=above_enabled,
            hide_above_minutes=above_minutes,
        )
        changed = updated_context != context
        if changed:
            self._loaded_channel_context = updated_context
            self.apply_filter()
        return True

    def _validate_duration_filter_inputs(self) -> bool:
        self._normalize_empty_duration_filter_entries()
        below_enabled = bool(self.hide_below_enabled_var.get())
        above_enabled = bool(self.hide_above_enabled_var.get())
        below_minutes = self._parse_duration_filter_minutes("below")
        above_minutes = self._parse_duration_filter_minutes("above")

        if below_enabled and below_minutes is None:
            self._show_duration_filter_error(
                "“Ẩn video dưới” phải là số nguyên từ 1 đến 9999 phút.",
                "hide_below_entry",
            )
            return False
        if above_enabled and above_minutes is None:
            self._show_duration_filter_error(
                "“Ẩn video trên” phải là số nguyên từ 1 đến 9999 phút.",
                "hide_above_entry",
            )
            return False
        if below_enabled and above_enabled and above_minutes <= below_minutes:
            self._show_duration_filter_error(
                "Số phút “Ẩn video trên” phải lớn hơn số phút “Ẩn video dưới”.",
                "hide_above_entry",
            )
            return False
        return True

    def _duration_filter_value_in_range(self, value: int) -> bool:
        return DURATION_FILTER_MIN_MINUTES <= value <= DURATION_FILTER_MAX_MINUTES

    def _show_duration_filter_error(self, message: str, focus_attr: str) -> None:
        self._show_error_dialog(message)
        widget = getattr(self, focus_attr, None)
        if widget is None:
            return
        try:
            widget.focus_set()
        except Exception:
            pass

    def _capture_channel_request_context(self) -> _ChannelRequestContext:
        download_mode = self.download_mode_var.get()
        if download_mode not in DOWNLOAD_MODES:
            download_mode = MODE_VIDEO_THUMB
        self._normalize_empty_duration_filter_entries()
        return _ChannelRequestContext(
            save_folder=self.save_folder_var.get().strip(),
            download_mode=download_mode,
            hide_below_enabled=bool(self.hide_below_enabled_var.get()),
            hide_below_minutes=self._duration_filter_minutes("below"),
            hide_above_enabled=bool(self.hide_above_enabled_var.get()),
            hide_above_minutes=self._duration_filter_minutes("above"),
        )

    def _update_header_checkbox(self) -> None:
        visible = set(self.visible_orders)
        if not visible:
            header_text = "[ ]"
        elif visible.issubset(self.selected_orders):
            header_text = "[x]"
        elif visible.isdisjoint(self.selected_orders):
            header_text = "[ ]"
        else:
            header_text = "[-]"
        self.tree.heading("selected", text=header_text, anchor="center")

    def _selected_visible_videos(self) -> list:
        by_order = {video.display_order: video for video in self.videos}
        selected = []
        for row_id in self.tree.get_children():
            order = int(row_id)
            if order in self.selected_orders and order in by_order:
                selected.append(by_order[order])
        return selected

    def _selected_loaded_videos(self) -> list:
        return [video for video in self.videos if video.display_order in self.selected_orders]

    def _videos_for_snapshot_ids(self, video_ids: list[str]) -> list:
        by_id = {video.video_id: video for video in self.videos}
        return [by_id[video_id] for video_id in video_ids if video_id in by_id]

    def _video_for_order(self, order: int):
        for video in self.videos:
            if video.display_order == order:
                return video
        return None

    def _paths_for_video(self, video):
        if not self.channel_info:
            return None
        save_folder = self.save_folder_var.get().strip()
        if not save_folder:
            return None
        filename_base = getattr(video, "sanitized_filename_base", "") or getattr(video, "title", "")
        return build_output_paths(save_folder, self.channel_info.channel_name, filename_base)

    def _save_manual_status(self, video, status: str) -> None:
        if self.downloading:
            return
        if status not in SUPPORTED_STATUS_VALUES:
            return
        if not self.channel_info:
            self._append_log("[ERROR] No channel loaded")
            return

        paths = self._paths_for_video(video)
        try:
            update_manual_status(
                self.channel_info.channel_id,
                self.channel_info.channel_name,
                self.save_folder_var.get().strip(),
                video,
                status,
                paths=paths,
            )
        except OSError as exc:
            self._append_log(f"[ERROR] Could not save manual status: {exc}")
            self._show_error_dialog("Không thể lưu trạng thái thủ công.", detail=str(exc))
            return

        video.status = status
        self._append_log(f"[INFO] Manual status override applied: {video.title} -> {status}")
        self.apply_filter()

    def _apply_manual_status_to_selected(self, status: str) -> None:
        if self.downloading:
            return
        selected = self._selected_visible_videos()
        if not selected:
            return
        for video in selected:
            if not self.channel_info:
                break
            paths = self._paths_for_video(video)
            try:
                update_manual_status(
                    self.channel_info.channel_id,
                    self.channel_info.channel_name,
                    self.save_folder_var.get().strip(),
                    video,
                    status,
                    paths=paths,
                )
            except OSError as exc:
                self._append_log(f"[ERROR] Could not save manual status: {exc}")
                self._show_error_dialog("Không thể lưu trạng thái thủ công.", detail=str(exc))
                break
            video.status = status
            self._append_log(f"[INFO] Manual status override applied: {video.title} -> {status}")
        self.apply_filter()

    def _clear_manual_status_for_selected(self) -> None:
        if self.downloading:
            return
        selected = self._selected_visible_videos()
        if not selected or not self.channel_info:
            return

        for video in selected:
            entry = get_video_entry(self.channel_info.channel_id, video.video_id) or {}
            has_saved_paths = bool(entry.get("video_path") or entry.get("thumb_path") or entry.get("audio_path"))
            paths = None if has_saved_paths else self._paths_for_video(video)
            try:
                clear_manual_status(
                    self.channel_info.channel_id,
                    self.channel_info.channel_name,
                    self.save_folder_var.get().strip(),
                    video,
                    paths=paths,
                    download_mode=self.download_mode_var.get(),
                )
            except OSError as exc:
                self._append_log(f"[ERROR] Could not clear manual status: {exc}")
                self._show_error_dialog("Không thể xóa trạng thái thủ công.", detail=str(exc))
                break

        apply_statuses(
            self.videos,
            self.save_folder_var.get().strip(),
            self.channel_info.channel_name,
            self.channel_info.channel_id,
            download_mode=self.download_mode_var.get(),
            warning_callback=self._append_log,
        )
        self.apply_filter()

    def _update_download_button_text(self) -> None:
        count = len(set(self.visible_orders).intersection(self.selected_orders))
        self.download_button.configure(text=f"Tải ({count}) video đã chọn")

    def _update_more_button_state(self) -> None:
        if not hasattr(self, "more_button"):
            return
        can_load_more = bool(
            self.channel_info
            and self.next_page_token
            and not self.loading_more
            and not self.fetching
            and not self.downloading
            and not self.shutdown_in_progress
        )
        self.more_button.configure(state="normal" if can_load_more else "disabled")

    def _update_stop_button_state(self) -> None:
        if hasattr(self, "stop_button"):
            enabled = self.downloading and not self.download_stop_requested
            self.stop_button.configure(state="normal" if enabled else "disabled")

    def _set_download_controls_locked(self, locked: bool) -> None:
        self._refresh_interaction_control_states(force_download_locked=locked)

    def _channel_request_busy(self) -> bool:
        return bool(getattr(self, "fetching", False) or getattr(self, "loading_more", False))

    def _refresh_interaction_control_states(self, force_download_locked: bool | None = None) -> None:
        channel_busy = self._channel_request_busy()
        download_busy = bool(self.downloading)
        shutdown_busy = bool(self.shutdown_in_progress)
        download_locked = bool(force_download_locked) if force_download_locked is not None else download_busy
        semantic_locked = channel_busy or download_locked or shutdown_busy
        download_only_locked = download_locked or shutdown_busy

        semantic_state = "disabled" if semantic_locked else "normal"
        semantic_combo_state = "disabled" if semantic_locked else "readonly"
        download_state = "disabled" if download_only_locked else "normal"
        download_combo_state = "disabled" if download_only_locked else "readonly"
        self._configure_widget_state("api_key_entry", semantic_state)
        self._configure_widget_state("channel_entry", semantic_state)
        self._configure_widget_state("fetch_button", semantic_state)
        self._configure_widget_state("choose_folder_button", semantic_state)
        self._configure_widget_state("mode_box", semantic_combo_state)
        self._configure_widget_state("hide_below_check", semantic_state)
        self._configure_widget_state("hide_above_check", semantic_state)
        self._refresh_duration_entry_states(locked=semantic_locked)
        self._configure_widget_state("download_button", "disabled" if semantic_locked else "normal")

        self._configure_widget_state("select_by_date_button", download_state)
        self._configure_widget_state("cookies_check", download_state)
        self._configure_widget_state("speed_limit_entry", download_state)
        self._configure_widget_state("file_start_number_entry", download_state)
        self._configure_widget_state("download_engine_box", download_combo_state)
        self._configure_widget_state("filter_box", download_combo_state)
        self._update_cookies_state()
        self._update_more_button_state()
        self._update_stop_button_state()
        self._update_download_button_text()

    def _configure_widget_state(self, attr_name: str, state: str) -> None:
        widget = getattr(self, attr_name, None)
        if widget is not None:
            widget.configure(state=state)

    def _refresh_duration_entry_states(self, locked: bool | None = None) -> None:
        if locked is None:
            locked = bool(self._channel_request_busy() or self.downloading or self.shutdown_in_progress)
        below_state = "disabled" if locked or not bool(self.hide_below_enabled_var.get()) else "normal"
        above_state = "disabled" if locked or not bool(self.hide_above_enabled_var.get()) else "normal"
        self._configure_widget_state("hide_below_entry", below_state)
        self._configure_widget_state("hide_above_entry", above_state)

    def _finish_download_ui(self) -> None:
        if self.shutdown_in_progress or self.exit_after_download_stop:
            self._schedule_shutdown_poll()
            return
        self.downloading = False
        self.download_worker = None
        self.download_controller = None
        self.download_stop_requested = False
        self.cancel_download = False
        self.exit_after_download_stop = False
        self.close_requested = False
        self._download_terminal_received = False
        self._download_terminal_outcome = ""
        self._download_terminal_message = ""
        self._active_download_run_id = None
        self._set_download_controls_locked(False)

    def _on_close(self) -> None:
        if self._root_destroyed or self.shutdown_in_progress:
            return
        if self.close_requested:
            return
        if not self.downloading:
            self._destroy_root_once()
            return
        if self._confirm_exit_while_downloading():
            self._exit_while_downloading()

    def _confirm_exit_while_downloading(self) -> bool:
        return show_confirm_dialog(
            self.root,
            title="Đang tải video",
            message="Tiến trình tải vẫn đang chạy. Bạn muốn thoát ứng dụng hay tiếp tục tải?",
            confirm_text="Thoát",
            cancel_text="Tiếp tục",
        )

    def _exit_while_downloading(self) -> None:
        if self.shutdown_in_progress:
            return
        self.shutdown_in_progress = True
        self.shutdown_started_at = time.monotonic()
        self._shutdown_slow_warning_logged = False
        self._shutdown_cancel_reissued = False
        self._request_download_stop(
            exit_after=True,
            log_message="[WARNING] Người dùng chọn thoát ứng dụng khi đang tải.",
            info_message="[INFO] Đang dừng tiến trình và chờ hoàn tất dọn dẹp...",
            emit_stop_progress=False,
        )
        self.progress_current_var.set("Đang tải: Đang dừng để thoát...")
        self.progress_detail_var.set("Đang xử lý: Chờ tiến trình tải kết thúc an toàn")
        self._set_download_controls_locked(True)
        self._update_stop_button_state()
        self._schedule_shutdown_poll()

    def _after(self, delay_ms: int, callback):
        if self._root_destroyed:
            return None
        try:
            return self.root.after(delay_ms, callback)
        except tk.TclError:
            return None

    def _cancel_after_id(self, after_id) -> None:
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except tk.TclError:
            pass

    def _schedule_shutdown_poll(self) -> None:
        if self._root_destroyed or not self.shutdown_in_progress:
            return
        if self._shutdown_poll_after_id is not None:
            return
        self._shutdown_poll_after_id = self._after(SHUTDOWN_POLL_MS, self._poll_shutdown_completion)

    def _poll_shutdown_completion(self) -> None:
        if self._root_destroyed:
            return
        self._shutdown_poll_after_id = None

        worker_alive = self._download_worker_alive()
        controller_active = self._controller_has_active_process()
        terminal_received = self._download_terminal_received or self.download_worker is None

        if terminal_received and not worker_alive and not controller_active:
            self.download_worker = None
            self.download_controller = None
            self._clear_progress_queue()
            self._destroy_root_once()
            return

        self._handle_slow_shutdown(controller_active)
        self._schedule_shutdown_poll()

    def _handle_slow_shutdown(self, controller_active: bool) -> None:
        if self.shutdown_started_at is None:
            return
        elapsed = time.monotonic() - self.shutdown_started_at
        if elapsed < SHUTDOWN_SLOW_WARNING_SECONDS:
            return
        if not self._shutdown_slow_warning_logged:
            self._append_log(
                "[WARNING] Tiến trình đang mất nhiều thời gian để dừng; "
                "ứng dụng vẫn chờ dọn dẹp an toàn."
            )
            self._shutdown_slow_warning_logged = True
        if controller_active and not self._shutdown_cancel_reissued and self.download_controller is not None:
            self.download_controller.request_cancel()
            self._shutdown_cancel_reissued = True

    def _schedule_download_finish_poll(self) -> None:
        if self._root_destroyed or self.shutdown_in_progress:
            return
        if self._download_finish_poll_after_id is not None:
            return
        self._download_finish_poll_after_id = self._after(SHUTDOWN_POLL_MS, self._poll_download_finish_completion)

    def _poll_download_finish_completion(self) -> None:
        if self._root_destroyed:
            return
        self._download_finish_poll_after_id = None
        if self.shutdown_in_progress:
            self._schedule_shutdown_poll()
            return
        if not self._download_terminal_received:
            return
        if self._download_worker_alive() or self._controller_has_active_process():
            self._schedule_download_finish_poll()
            return
        self._finish_download_ui()

    def _download_worker_alive(self) -> bool:
        worker = self.download_worker
        return bool(worker is not None and worker.is_alive())

    def _controller_has_active_process(self) -> bool:
        controller = self.download_controller
        return bool(controller is not None and controller.has_active_process())

    def _destroy_root_once(self) -> None:
        if self._root_destroyed:
            return
        self._root_destroyed = True
        for attr_name in ("_shutdown_poll_after_id", "_download_finish_poll_after_id"):
            after_id = getattr(self, attr_name, None)
            setattr(self, attr_name, None)
            self._cancel_after_id(after_id)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _next_channel_request_id(self) -> int:
        self._channel_request_sequence += 1
        return self._channel_request_sequence

    def _queue_channel_request_log(self, request_token, message: str) -> None:
        self.events.put(("channel_request_log", request_token, sanitize_log_text(message)))

    def _restore_loaded_generation_after_failed_fetch(self) -> None:
        if self._loaded_channel_generation is not None:
            self._channel_generation = self._loaded_channel_generation

    def _clear_pending_fetch_manual_key(self, token: _FetchRequestToken | None = None) -> None:
        if token is not None and self._active_fetch_manual_key_request_id != token.request_id:
            return
        self._active_fetch_manual_key = ""
        self._active_fetch_manual_key_request_id = None

    def _persist_accepted_fetch_manual_key(self, token: _FetchRequestToken) -> None:
        if self._active_fetch_manual_key_request_id != token.request_id:
            return
        manual_key = self._active_fetch_manual_key.strip()
        self._clear_pending_fetch_manual_key(token)
        if not manual_key:
            return
        if not save_last_api_key(manual_key):
            self._append_log("[WARNING] Không thể lưu API key bằng bảo vệ Windows.")

    def _is_current_channel_request_log(self, request_token) -> bool:
        if isinstance(request_token, _FetchRequestToken):
            return self._is_current_fetch_request(request_token)
        if isinstance(request_token, _LoadMoreRequestToken):
            return self._is_current_load_more_request(request_token)
        return False

    def _is_current_fetch_request(self, token: _FetchRequestToken) -> bool:
        active = self._active_fetch_request
        return bool(
            isinstance(token, _FetchRequestToken)
            and isinstance(active, _FetchRequestToken)
            and token.generation == self._channel_generation
            and token.request_id == active.request_id
            and token.generation == active.generation
        )

    def _is_current_load_more_request(self, token: _LoadMoreRequestToken) -> bool:
        active = self._active_load_more_request
        channel = self.channel_info
        return bool(
            isinstance(token, _LoadMoreRequestToken)
            and isinstance(active, _LoadMoreRequestToken)
            and token.generation == self._channel_generation
            and token.generation == self._loaded_channel_generation
            and token.request_id == active.request_id
            and token.generation == active.generation
            and channel is not None
            and str(getattr(channel, "channel_id", "") or "") == token.channel_id
            and str(getattr(channel, "uploads_playlist_id", "") or "") == token.uploads_playlist_id
            and str(self.next_page_token or "") == token.page_token
            and len(self.videos) + 1 == token.start_order
        )

    def _thread_log(self, message: str) -> None:
        self.events.put(("log", sanitize_log_text(message)))

    def _enqueue_progress_event(self, event: ProgressEvent) -> None:
        put_latest_progress_event(self.progress_queue, event)

    def _clear_progress_queue(self) -> None:
        try:
            while True:
                self.progress_queue.get_nowait()
        except queue.Empty:
            pass

    def _poll_progress_queue(self) -> None:
        if self._root_destroyed:
            return
        latest = None
        try:
            while True:
                latest = self.progress_queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            display_event = self._merge_progress_event_for_display(latest)
            current_line, detail_line = self._localized_progress_lines(display_event)
            self.progress_current_var.set(current_line)
            self.progress_detail_var.set(detail_line)
        self._after(300, self._poll_progress_queue)

    def _localized_progress_lines(self, event: ProgressEvent) -> tuple[str, str]:
        current_line, detail_line = format_progress_event_lines(event)
        return self._localize_progress_line(current_line), self._localize_progress_line(detail_line)

    def _localize_progress_line(self, line: str) -> str:
        replacements = {
            "Downloading: Ready": "Đang tải: Sẵn sàng",
            "Downloading: Batch completed": "Đang tải: Hoàn tất danh sách",
            "Downloading: Stop requested": "Đang tải: Đã yêu cầu dừng",
            "Processing: Cancelling current process...": "Đang xử lý: Đang hủy tiến trình hiện tại...",
            "Processing: -": "Đang xử lý: -",
        }
        if line in replacements:
            return replacements[line]
        if line.startswith("Downloading:"):
            return "Đang tải:" + line[len("Downloading:") :]
        if line.startswith("Processing:"):
            return "Đang xử lý:" + line[len("Processing:") :]
        return line

    def _reset_progress_sticky(self, *, reset_order: bool = False) -> None:
        self._progress_display_key = None
        self._progress_sticky_percent = None
        self._progress_sticky_speed = None
        self._progress_sticky_fragment = None
        self._progress_sticky_source = None
        if reset_order or not hasattr(self, "_progress_latest_item_key"):
            self._progress_latest_item_key = None
            self._progress_latest_generation = None
            self._progress_last_display_event = None
            self._progress_terminal_guard = False

    def _merge_progress_event_for_display(self, event: ProgressEvent) -> ProgressEvent:
        item_key = (event.video_total, event.video_index, event.title)
        terminal = event.kind in {"batch_complete", "stop_requested", "error"}
        if terminal:
            self._reset_progress_sticky()
            if event.video_index is not None:
                if self._progress_latest_item_key != item_key:
                    self._progress_latest_generation = None
                self._progress_latest_item_key = item_key
            if event.generation is not None:
                self._progress_latest_generation = event.generation
            self._progress_terminal_guard = True
            self._progress_last_display_event = event
            return event

        latest_item = self._progress_latest_item_key
        if (
            latest_item is not None
            and event.video_index is not None
            and latest_item[1] is not None
            and event.video_total == latest_item[0]
            and event.video_index < latest_item[1]
        ):
            return self._progress_last_display_event or event

        same_item = latest_item == item_key
        if same_item and event.generation is not None and self._progress_latest_generation is not None:
            if event.generation < self._progress_latest_generation:
                return self._progress_last_display_event or event

        newer_generation = bool(
            same_item
            and event.generation is not None
            and (
                self._progress_latest_generation is None
                or event.generation > self._progress_latest_generation
            )
        )
        new_item = latest_item != item_key
        if self._progress_terminal_guard and not (new_item or newer_generation):
            return self._progress_last_display_event or event
        if new_item or newer_generation:
            self._reset_progress_sticky()
            self._progress_terminal_guard = False
            self._progress_latest_item_key = item_key
            if new_item:
                self._progress_latest_generation = None
        if event.generation is not None:
            self._progress_latest_generation = event.generation

        key = (event.video_index, event.video_total, event.phase, event.title, event.generation)
        if key != self._progress_display_key:
            self._reset_progress_sticky()
            self._progress_display_key = key

        if event.percent:
            self._progress_sticky_percent = event.percent
        if event.speed:
            self._progress_sticky_speed = event.speed
        if event.fragment:
            self._progress_sticky_fragment = event.fragment
        if event.source:
            self._progress_sticky_source = event.source

        display_event = ProgressEvent(
            kind=event.kind,
            phase=event.phase,
            message=event.message,
            video_index=event.video_index,
            video_total=event.video_total,
            title=event.title,
            percent=event.percent or self._progress_sticky_percent,
            speed=event.speed or self._progress_sticky_speed,
            eta=None,
            fragment=event.fragment or self._progress_sticky_fragment,
            source=event.source or self._progress_sticky_source,
            generation=event.generation,
        )
        self._progress_last_display_event = display_event
        return display_event

    def _append_log(self, message: str) -> None:
        safe_message = sanitize_log_text(message)
        self.log_text.configure(state="normal")
        for source_line, display_line in timestamp_log_lines(safe_message):
            tag = self._log_tag_for(source_line)
            if tag:
                self.log_text.insert("end", display_line + "\n", tag)
            else:
                self.log_text.insert("end", display_line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _log_tag_for(self, message: str) -> str | None:
        if message.startswith("[SUCCESS]"):
            return "success"
        if message.startswith("[ERROR]"):
            return "error"
        if message.startswith("[SKIP]"):
            return "skip"
        if message.startswith("[WARNING]"):
            return "warning"
        return None

    def _friendly_api_message(self, exc: YoutubeApiError) -> str:
        return format_friendly_error(classify_api_error(exc.code, exc.message), [exc.message])

    def _friendly_general_message(self, message: str) -> str:
        return format_friendly_error(classify_general_error(message), [message])

    def _show_error_dialog(self, message: str, title: str = "Lỗi", detail: str | None = None) -> None:
        clean_message = self._dialog_message_without_log_prefix(message)
        show_error_dialog(
            self.root,
            title,
            clean_message,
            detail=detail,
            heading=self._dialog_error_heading(clean_message, title),
        )

    def _dialog_message_without_log_prefix(self, message: str) -> str:
        text = (message or "").strip()
        for prefix in ("[ERROR]", "[WARNING]", "[INFO]", "[SUCCESS]", "[SKIP]"):
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
        return text

    def _dialog_error_heading(self, message: str, title: str) -> str | None:
        if title.strip() != "Lỗi":
            return None
        first_line = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
        if first_line.startswith("Không tìm thấy kênh"):
            return "Không tìm thấy kênh"
        return None

    def _handle_systemic_download_block(self, context: SystemicBlockContext) -> None:
        controller = self.download_controller
        if self._systemic_block_should_stop(controller, context):
            self._submit_systemic_stop(controller, context)
            return

        reason_line = (context.reason or "").splitlines()[0] if context.reason else context.failure_kind.value
        self.progress_current_var.set("Đang tải: Tạm dừng")
        self.progress_detail_var.set(f"Đang xử lý: {reason_line}")
        self._append_log(f"[WARNING] Danh sách tải đã tạm dừng: {context.failure_kind.value}")

        retry_text = "Thử lại video hiện tại"
        skip_text = "Bỏ qua video này"
        stop_text = "Dừng danh sách"
        self._append_log(
            "[YT-DLP PAUSE] "
            f"part={context.part or 'unknown'} "
            f"stage={context.stage or 'unknown'} "
            f"exit_code={context.exit_code if context.exit_code is not None else 'unknown'} "
            f"failure_kind={context.failure_kind.value}"
        )
        for line in context.output_lines[:4]:
            if line.strip():
                self._append_log(f"[YT-DLP PAUSE FATAL] {line}")

        buttons = []
        if context.retry_allowed:
            buttons.append({"text": retry_text, "value": BatchDecision.RETRY_CURRENT.value, "width": 24})
        buttons.extend(
            (
                {"text": skip_text, "value": BatchDecision.SKIP_CURRENT.value, "width": 18},
                {"text": stop_text, "value": BatchDecision.STOP_BATCH.value, "width": 18},
            )
        )

        controller = self.download_controller
        if self._systemic_block_should_stop(controller, context):
            self._submit_systemic_stop(controller, context)
            return

        decision = show_app_dialog(
            self.root,
            title="Danh sách tải đã tạm dừng",
            message=self._systemic_block_dialog_message(context),
            buttons=tuple(buttons),
            default_button=retry_text if context.retry_allowed else skip_text,
            cancel_button=stop_text,
            width=620,
        )
        if decision not in {item.value for item in BatchDecision}:
            decision = BatchDecision.STOP_BATCH.value

        controller = self.download_controller
        if self._systemic_block_should_stop(controller, context):
            self._submit_systemic_stop(controller, context)
            return
        if controller is not None and not controller.submit_systemic_decision(context.block_id, decision):
            self._append_log("[INFO] Quyết định tạm dừng đã hết hiệu lực.")

    def _systemic_block_should_stop(
        self,
        controller: DownloadController | None,
        context: SystemicBlockContext,
    ) -> bool:
        if (
            not self.downloading
            or self.download_stop_requested
            or controller is None
            or controller.is_cancel_requested()
        ):
            return True
        is_active = getattr(controller, "is_systemic_block_active", None)
        if is_active is not None and not is_active(context.block_id):
            return True
        return False

    def _submit_systemic_stop(
        self,
        controller: DownloadController | None,
        context: SystemicBlockContext,
    ) -> None:
        if controller is not None:
            controller.submit_systemic_decision(context.block_id, BatchDecision.STOP_BATCH.value)

    def _systemic_block_dialog_message(self, context: SystemicBlockContext) -> str:
        title = context.title or context.video_id or "-"
        part = self._localized_systemic_part(context.part)
        stage = context.stage or "unknown"
        exit_code = str(context.exit_code) if context.exit_code is not None else "unknown"
        fatal_line = next((line for line in context.output_lines if line.strip()), "")
        cookie_source = context.cookie_source or "-"
        cookie_path = context.cookie_path or "-"
        retry_guidance = (
            "Hệ thống chỉ thử lại khi file cookie nguồn đã thay đổi sau lần lỗi."
            if context.retry_allowed
            else "Lần thử lại cookie đã được dùng cho phần này; bạn có thể bỏ qua video hoặc dừng danh sách."
        )
        return "\n".join(
            [
                f"Lý do: {context.reason}",
                f"Video: {title}",
                f"Phần: {part}",
                f"Nguồn cookie: {cookie_source}",
                f"Stage: {stage}",
                f"Type: {context.failure_kind.value}",
                f"Exit code: {exit_code}",
                f"Fatal: {fatal_line}" if fatal_line else "Fatal: -",
                f"File cookie: {cookie_path}",
                "",
                "Các file và phần đã tải xong sẽ được giữ nguyên.",
                retry_guidance,
            ]
        )

    def _localized_systemic_part(self, part: str) -> str:
        if part == "video":
            return "Video"
        if part == "audio":
            return "MP3"
        if part == "thumb":
            return "Thumbnail"
        return part or "-"

    def _process_events(self) -> None:
        if self._root_destroyed:
            return
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self._after(100, self._process_events)

    def _handle_event(self, event) -> None:
        kind = event[0]
        if kind == "log":
            self._append_log(event[1])
        elif kind == "channel_request_log":
            token = event[1]
            if not self._is_current_channel_request_log(token):
                return
            self._append_log(event[2])
        elif kind == "fetch_done":
            token = event[1]
            if not self._is_current_fetch_request(token):
                return
            self.channel_info = event[2]
            self.selected_orders.clear()
            self.videos = event[3]
            self.next_page_token = event[4]
            self._loaded_channel_generation = token.generation
            self._loaded_channel_context = token.context
            self._active_fetch_request = None
            self.fetching = False
            self._persist_accepted_fetch_manual_key(token)
            self._refresh_interaction_control_states()
            self._set_resolved_channel_display(self.channel_info)
            self.apply_filter()
        elif kind == "fetch_error":
            token = event[1]
            if not self._is_current_fetch_request(token):
                return
            self._append_log(event[2])
            self._active_fetch_request = None
            self.fetching = False
            self._restore_loaded_generation_after_failed_fetch()
            self._clear_pending_fetch_manual_key(token)
            self._refresh_interaction_control_states()
        elif kind == "load_more_done":
            token = event[1]
            if not self._is_current_load_more_request(token):
                return
            more_videos = event[2]
            next_page_token = event[3]
            self.videos.extend(more_videos)
            if self.channel_info:
                apply_statuses(
                    self.videos,
                    token.context.save_folder,
                    self.channel_info.channel_name,
                    self.channel_info.channel_id,
                    download_mode=token.context.download_mode,
                    warning_callback=self._append_log,
                )
            self.next_page_token = next_page_token
            self._active_load_more_request = None
            self.loading_more = False
            self._refresh_interaction_control_states()
            self.apply_filter()
        elif kind == "load_more_error":
            token = event[1]
            if not self._is_current_load_more_request(token):
                return
            self._append_log(event[2])
            self.loading_more = False
            self._active_load_more_request = None
            self._refresh_interaction_control_states()
        elif kind == "status_update":
            order, status = event[1], event[2]
            for video in self.videos:
                if video.display_order == order:
                    video.status = status
                    break
            self.apply_filter()
        elif kind == "download_video_completed_for_numbering":
            self._handle_download_video_completed_for_numbering(event)
        elif kind == "systemic_download_block":
            self._handle_systemic_download_block(event[1])
        elif kind == "download_worker_finished":
            outcome = event[1] if len(event) > 1 else "completed"
            message = event[2] if len(event) > 2 else ""
            self._handle_download_worker_finished(outcome, message)
        elif kind == "download_done":
            self._handle_download_worker_finished("completed", "")
        elif kind == "download_error":
            self._handle_download_worker_finished("error", event[1])

    def _handle_download_video_completed_for_numbering(self, event) -> None:
        if not isinstance(event, (tuple, list)) or len(event) < 3:
            return
        run_id = event[1]
        try:
            video_id = str(event[2] or "").strip()
        except Exception:
            return
        if run_id != getattr(self, "_active_download_run_id", None) or not video_id:
            return
        if video_id not in getattr(self, "_download_run_selected_ids", set()):
            return
        if video_id in getattr(self, "_download_run_initial_complete_ids", set()):
            return
        completed_ids = getattr(self, "_download_run_completed_ids", None)
        run_start_number = getattr(self, "_download_run_start_number", None)
        if not isinstance(completed_ids, set) or not isinstance(run_start_number, int):
            return
        if video_id in completed_ids:
            return

        # This advances only the next-run suggestion; active-batch allocation stays unchanged.
        completed_ids.add(video_id)
        next_number = run_start_number + len(completed_ids)
        try:
            self.file_start_number_var.set(str(next_number))
            self._append_log(
                f"[INFO] File start number advanced to {next_number} after a completed video."
            )
        except (AttributeError, tk.TclError):
            return

    def _handle_download_worker_finished(self, outcome: str, message: str = "") -> None:
        if self._download_terminal_received:
            return
        self._download_terminal_received = True
        self._download_terminal_outcome = outcome or "completed"
        self._download_terminal_message = message or ""
        if self._download_terminal_outcome == "error" and self._download_terminal_message:
            self._append_log(self._download_terminal_message)

        if self.shutdown_in_progress or self.exit_after_download_stop:
            self._schedule_shutdown_poll()
            self._poll_shutdown_completion()
            return

        self._poll_download_finish_completion()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    YouTubeDownloaderWindow(root)
    root.deiconify()
    root.mainloop()

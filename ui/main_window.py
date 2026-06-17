import queue
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, font, ttk

from core.download_modes import DOWNLOAD_MODES, MODE_VIDEO_THUMB
from core.downloader import (
    COOKIE_SOURCE_BRIDGE,
    COOKIE_SOURCE_FILE,
    DownloadController,
    DownloadError,
    DownloadOptions,
    validate_download_environment,
    validate_speed_limit,
    download_items,
)
from core.app_settings import (
    load_bridge_cookie_path,
    load_cookie_source,
    load_last_api_key,
    save_bridge_cookie_path,
    save_cookie_source,
    save_last_api_key,
)
from core.error_messages import classify_api_error, classify_general_error, format_friendly_error
from core.file_status import apply_statuses, build_output_paths, should_show_not_downloaded
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
    update_manual_status,
)
from core.youtube_api import (
    SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS,
    YoutubeApiError,
    fetch_latest_video_page,
    fetch_more_videos,
    is_short_video,
    mask_api_key,
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
SHORT_VIDEO_THRESHOLD_MINUTES = ("1", "2", "3", "5", "10")
DEFAULT_SHORT_VIDEO_THRESHOLD_MINUTES = str(SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS // 60)
COOKIE_STATUS_POLL_MS = 4000
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


class YouTubeDownloaderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube Downloaderbs")
        self.root.geometry("1440x640")
        self.root.minsize(1000, 640)

        self.events: queue.Queue = queue.Queue()
        self.progress_queue: queue.Queue = queue.Queue(maxsize=1)
        self.channel_info = None
        self.videos = []
        self.selected_orders: set[int] = set()
        self.visible_orders: list[int] = []
        self.next_page_token = ""
        self.fetching = False
        self.loading_more = False
        self.downloading = False
        self.download_controller: DownloadController | None = None
        self.download_stop_requested = False
        self.exit_after_download_stop = False
        self.close_requested = False
        self.cancel_download = False
        self.status_editor = None

        self.api_key_var = tk.StringVar(value=load_last_api_key())
        self.channel_var = tk.StringVar()
        self.save_folder_var = tk.StringVar()
        self.cookies_enabled_var = tk.BooleanVar(value=False)
        self.cookies_path_var = tk.StringVar()
        self.cookie_source_var = tk.StringVar(value=COOKIE_SOURCE_LABELS[load_cookie_source()])
        self.bridge_cookie_path_var = tk.StringVar(value=load_bridge_cookie_path())
        self.cookie_status_var = tk.StringVar()
        self.speed_limit_var = tk.StringVar()
        self.download_mode_var = tk.StringVar(value=MODE_VIDEO_THUMB)
        self.show_short_videos_var = tk.BooleanVar(value=False)
        self.short_video_threshold_var = tk.StringVar(value=DEFAULT_SHORT_VIDEO_THRESHOLD_MINUTES)
        self.filter_var = tk.StringVar(value=FILTER_ALL)
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar()
        self.progress_current_var = tk.StringVar(value="Downloading: Ready")
        self.progress_detail_var = tk.StringVar(value="Processing: -")
        self._reset_progress_sticky()
        self.search_match_orders: list[int] = []
        self.current_search_match_index = -1
        self.tree_column_drag: dict | None = None
        self.tree_column_ratios: dict[str, float] = self._default_tree_column_ratios()
        self.tree_column_fit_after_id = None
        self.tree_column_fit_in_progress = False

        self._build_ui()
        self.search_var.trace_add("write", lambda *_args: self._on_search_text_changed())
        self.cookies_path_var.trace_add("write", lambda *_args: self._refresh_cookie_status())
        self.bridge_cookie_path_var.trace_add("write", lambda *_args: self._refresh_cookie_status())
        self._update_cookies_state()
        self._refresh_cookie_status()
        self._update_download_button_text()
        self._update_more_button_state()
        self._update_stop_button_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_events)
        self.root.after(300, self._poll_progress_queue)
        self.root.after(COOKIE_STATUS_POLL_MS, self._poll_cookie_status)

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
        secondary_bg = "#eaf3ff"
        secondary_active = "#dbeafe"
        secondary_fg = "#175f9f"

        self.root.configure(background=app_bg)
        style.configure(".", font=self._ui_font, background=app_bg, foreground=text)
        style.configure("TFrame", background=app_bg)
        style.configure("TLabel", background=app_bg, foreground=text)
        style.configure("TCheckbutton", background=app_bg, foreground=text)
        style.configure(
            "TEntry",
            fieldbackground=panel_bg,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=(5, 3),
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
            padding=(4, 2),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", panel_bg), ("disabled", "#eef2f7")],
            foreground=[("disabled", muted)],
            arrowcolor=[("disabled", "#9aa4b2")],
            bordercolor=[("focus", "#8fc5f5")],
        )
        style.configure("TButton", padding=(9, 4), background="#f8fafc", foreground=text, bordercolor=border)
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
            padding=(12, 5),
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
            padding=(10, 4),
            font=self._button_font,
        )
        style.map(
            "SecondaryAccent.TButton",
            background=[("active", secondary_active), ("pressed", "#cfe4ff"), ("disabled", "#eef2f7")],
            foreground=[("active", "#0f4f85"), ("disabled", "#7a8796")],
            bordercolor=[("active", "#8fc5f5"), ("disabled", "#d8dee6")],
        )
        style.configure("CookieStatus.TLabel", foreground=muted, background=app_bg, font=(ui_family, 8))
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
        right.rowconfigure(0, weight=5)
        right.rowconfigure(1, weight=1)

        source_frame = ttk.LabelFrame(left, text="Source", padding=(12, 10), style="Grouped.TLabelframe")
        source_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        source_frame.columnconfigure(1, weight=1)

        ttk.Label(source_frame, text="YouTube API Key").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.api_key_entry = ttk.Entry(source_frame, textvariable=self.api_key_var)
        self.api_key_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)

        ttk.Label(source_frame, text="Channel URL / Channel ID / Handle").grid(
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

        filter_frame = ttk.LabelFrame(left, text="Filters", padding=(12, 10), style="Grouped.TLabelframe")
        filter_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        filter_frame.columnconfigure(1, weight=1)
        ttk.Label(filter_frame, text="Filter").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.filter_box = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=(FILTER_ALL, FILTER_NOT_DOWNLOADED),
            state="readonly",
            width=32,
        )
        self.filter_box.grid(row=0, column=1, sticky="ew", pady=2)
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_filter())

        ttk.Label(filter_frame, text="Tìm kiếm").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
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

        self.short_videos_check = ttk.Checkbutton(
            filter_frame,
            text="Hiển thị video ngắn",
            variable=self.show_short_videos_var,
            command=self._on_short_video_filter_changed,
        )
        self.short_videos_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 2))

        threshold_frame = ttk.Frame(filter_frame)
        threshold_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Label(threshold_frame, text="Ẩn video dưới:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.threshold_box = ttk.Combobox(
            threshold_frame,
            textvariable=self.short_video_threshold_var,
            values=SHORT_VIDEO_THRESHOLD_MINUTES,
            state="readonly",
            width=4,
        )
        self.threshold_box.grid(row=0, column=1, sticky="w")
        self.threshold_box.bind("<<ComboboxSelected>>", lambda _event: self._on_short_video_filter_changed())
        ttk.Label(threshold_frame, text="phút").grid(row=0, column=2, sticky="w", padx=(4, 0))

        table_group = ttk.LabelFrame(right, text="Video list", padding=(12, 10), style="Grouped.TLabelframe")
        table_group.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        table_group.columnconfigure(0, weight=1)
        table_group.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(table_group)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(table_frame, columns=TREE_COLUMN_IDS, show="headings", selectmode="browse")
        self.tree.heading("selected", text="[ ]", anchor="center")
        self.tree.heading("title", text="Video title")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("published", text="Upload date")
        self.tree.heading("status", text="Status")
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
        self.status_menu = tk.Menu(self.root, tearoff=0)
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

        output_frame = ttk.LabelFrame(left, text="Output & Cookies", padding=(10, 8), style="Grouped.TLabelframe")
        output_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Save folder").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
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
        )
        self.cookies_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))

        ttk.Label(output_frame, text="Cookies source").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        self.cookie_source_box = ttk.Combobox(
            output_frame,
            textvariable=self.cookie_source_var,
            values=tuple(COOKIE_SOURCE_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.cookie_source_box.grid(row=2, column=1, sticky="w", pady=2)
        self.cookie_source_box.bind("<<ComboboxSelected>>", lambda _event: self._on_cookie_source_changed())

        self.cookies_path_label = ttk.Label(output_frame, text="Cookie file path")
        self.cookies_path_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        self.cookies_entry = ttk.Entry(output_frame, textvariable=self.cookies_path_var, state="disabled")
        self.cookies_entry.grid(row=3, column=1, sticky="ew", pady=2)
        self.cookies_button = ttk.Button(output_frame, text="Chọn cookies*.txt", command=self.choose_cookies_file)
        self.cookies_button.configure(style="SecondaryAccent.TButton")
        self.cookies_button.grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=2)

        self.bridge_cookie_path_label = ttk.Label(output_frame, text="Bridge cookie path")
        self.bridge_cookie_path_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        self.bridge_cookie_entry = ttk.Entry(output_frame, textvariable=self.bridge_cookie_path_var, state="disabled")
        self.bridge_cookie_entry.grid(row=3, column=1, sticky="ew", pady=2)
        self.bridge_cookie_button = ttk.Button(
            output_frame,
            text="Chọn youtube_cookies.txt",
            command=self.choose_bridge_cookie_file,
        )
        self.bridge_cookie_button.configure(style="SecondaryAccent.TButton")
        self.bridge_cookie_button.grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=2)
        self.cookie_status_label = ttk.Label(
            output_frame,
            textvariable=self.cookie_status_var,
            style="CookieStatus.TLabel",
        )
        self.cookie_status_label.grid(row=4, column=1, columnspan=2, sticky="w", pady=(0, 2))

        download_frame = ttk.LabelFrame(left, text="Download", padding=(12, 10), style="Grouped.TLabelframe")
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

        ttk.Label(download_frame, text="Download limit").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        speed_frame = ttk.Frame(download_frame)
        speed_frame.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
        self.speed_limit_entry = ttk.Entry(speed_frame, textvariable=self.speed_limit_var, width=18)
        self.speed_limit_entry.grid(row=0, column=0, sticky="w")
        ttk.Label(speed_frame, text="MB/s").grid(row=0, column=1, sticky="w", padx=(6, 0))

        download_actions = ttk.Frame(download_frame)
        download_actions.grid(row=2, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.download_button = ttk.Button(download_actions, command=self.start_download, style="Primary.TButton")
        self.download_button.grid(row=0, column=0, sticky="e")
        self.stop_button = ttk.Button(download_actions, text="Dừng tải", command=self.stop_download)
        self.stop_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        progress_frame = ttk.LabelFrame(right, text="Progress / Logs", padding=(12, 10), style="Grouped.TLabelframe")
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

    def start_fetch(self) -> None:
        if self.fetching or self.downloading:
            return

        channel_input = self.channel_var.get().strip()
        if not channel_input:
            friendly = self._friendly_general_message("Cannot resolve channel")
            self._append_log(friendly)
            show_error_dialog(self.root, "Error", friendly)
            return

        manual_key = self.api_key_var.get().strip()
        save_folder = self.save_folder_var.get().strip()
        download_mode = self.download_mode_var.get()
        show_short_videos = self.show_short_videos_var.get()
        threshold_minutes = self._short_video_threshold_minutes()

        self.fetching = True
        self.loading_more = False
        self.next_page_token = ""
        self.fetch_button.configure(state="disabled")
        self._update_more_button_state()
        self.selected_orders.clear()
        self.videos = []
        self.apply_filter()

        worker = threading.Thread(
            target=self._fetch_worker,
            args=(channel_input, manual_key, save_folder, download_mode, show_short_videos, threshold_minutes),
            daemon=True,
        )
        worker.start()

    def _fetch_worker(
        self,
        channel_input: str,
        manual_key: str,
        save_folder: str,
        download_mode: str,
        show_short_videos: bool,
        threshold_minutes: int,
    ) -> None:
        try:
            channel, videos, next_page_token = fetch_latest_video_page(
                channel_input,
                manual_key,
                progress=self._thread_log,
                min_visible_duration_seconds=threshold_minutes * 60,
            )
            self._thread_log("[INFO] Checking local files...")
            apply_statuses(
                videos,
                save_folder,
                channel.channel_name,
                channel.channel_id,
                download_mode=download_mode,
                warning_callback=self._thread_log,
            )
            if manual_key.strip():
                if save_last_api_key(manual_key):
                    self._thread_log(f"[INFO] Saved last API key: {mask_api_key(manual_key)}")
                else:
                    self._thread_log("[WARNING] Could not save last API key.")
            hidden_short_videos = self._short_video_count(videos, threshold_minutes)
            if not show_short_videos and hidden_short_videos:
                self._thread_log(
                    f"[INFO] Hidden short videos: {hidden_short_videos} under {threshold_minutes} minutes."
                )
            self.events.put(("fetch_done", channel, videos, next_page_token))
            visible_count = len(videos) if show_short_videos else len(videos) - hidden_short_videos
            self._thread_log(f"[SUCCESS] Loaded {visible_count} videos after filtering short videos.")
        except YoutubeApiError as exc:
            self.events.put(("fetch_error", self._friendly_api_message(exc)))
        except Exception as exc:
            self.events.put(("fetch_error", self._friendly_general_message(str(exc) or "Network error")))

    def start_load_more(self) -> None:
        if self.loading_more or self.fetching or self.downloading:
            return
        if not self.channel_info or not self.next_page_token:
            self._append_log("[INFO] No more videos.")
            self.next_page_token = ""
            self._update_more_button_state()
            return

        self.loading_more = True
        self._update_more_button_state()
        worker = threading.Thread(
            target=self._load_more_worker,
            args=(
                self.channel_info.uploads_playlist_id,
                self.next_page_token,
                len(self.videos) + 1,
                self.api_key_var.get().strip(),
                self.show_short_videos_var.get(),
                self._short_video_threshold_minutes(),
            ),
            daemon=True,
        )
        worker.start()

    def _load_more_worker(
        self,
        uploads_playlist_id: str,
        page_token: str,
        start_order: int,
        manual_key: str,
        show_short_videos: bool,
        threshold_minutes: int,
    ) -> None:
        try:
            self._thread_log("[INFO] Loading next 100 videos...")
            videos, next_page_token = fetch_more_videos(
                uploads_playlist_id,
                page_token,
                start_order,
                manual_key,
                progress=self._thread_log,
                min_visible_duration_seconds=threshold_minutes * 60,
            )
            self.events.put(("load_more_done", videos, next_page_token))
            hidden_short_videos = self._short_video_count(videos, threshold_minutes)
            if not show_short_videos and hidden_short_videos:
                self._thread_log(
                    f"[INFO] Hidden short videos: {hidden_short_videos} under {threshold_minutes} minutes."
                )
            if videos:
                visible_count = len(videos) if show_short_videos else len(videos) - hidden_short_videos
                self._thread_log(f"[SUCCESS] Loaded {visible_count} more videos after filtering short videos.")
            if not next_page_token:
                self._thread_log("[INFO] No more videos.")
        except YoutubeApiError as exc:
            self.events.put(("load_more_error", self._friendly_api_message(exc)))
        except Exception as exc:
            self.events.put(("load_more_error", self._friendly_general_message(str(exc) or "Network error")))

    def choose_save_folder(self) -> None:
        if self.downloading:
            return
        folder = filedialog.askdirectory(title="Choose save folder")
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
            title="Choose cookies*.txt",
            filetypes=(("Cookies files", "cookies*.txt"), ("Text files", "*.txt"), ("All files", "*.*")),
        )
        if path:
            self.cookies_path_var.set(path)
            self._refresh_cookie_status()

    def choose_bridge_cookie_file(self) -> None:
        if self.downloading:
            return
        path = filedialog.askopenfilename(
            title="Choose youtube_cookies.txt",
            filetypes=(("YouTube cookies", "youtube_cookies.txt"), ("Text files", "*.txt"), ("All files", "*.*")),
        )
        if path:
            self.bridge_cookie_path_var.set(path)
            save_bridge_cookie_path(path)
            self._refresh_cookie_status()

    def _current_cookie_source(self) -> str:
        return COOKIE_SOURCE_VALUES_BY_LABEL.get(self.cookie_source_var.get(), COOKIE_SOURCE_FILE)

    def _on_cookie_source_changed(self) -> None:
        save_cookie_source(self._current_cookie_source())
        self._update_cookies_state()
        self._refresh_cookie_status()

    def _update_bridge_cookie_status(self) -> None:
        self._refresh_cookie_status()

    def _poll_cookie_status(self) -> None:
        self._refresh_cookie_status()
        self.root.after(COOKIE_STATUS_POLL_MS, self._poll_cookie_status)

    def _refresh_cookie_status(self) -> None:
        if not self.cookies_enabled_var.get():
            self.cookie_status_var.set("Cookies disabled")
            return

        if self._current_cookie_source() == COOKIE_SOURCE_BRIDGE:
            self.cookie_status_var.set(
                self._cookie_file_metadata_status("Bridge file", self.bridge_cookie_path_var.get().strip())
            )
            return

        self.cookie_status_var.set(
            self._cookie_file_metadata_status("Cookie file", self.cookies_path_var.get().strip())
        )

    def _cookie_file_metadata_status(self, label: str, path_text: str) -> str:
        if not path_text:
            return f"{label}: No file selected"

        path = Path(path_text)
        try:
            stat = path.stat()
            if not path.is_file():
                raise OSError
        except OSError:
            return f"{label}: Missing"

        updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return f"{label}: Found \u2022 {stat.st_size} bytes \u2022 updated {updated}"

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

    def _on_short_video_filter_changed(self) -> None:
        if self.downloading:
            return
        self.apply_filter()

    def open_select_by_date_dialog(self) -> None:
        if self.downloading:
            return
        if not self.videos:
            self._append_log("[ERROR] No videos loaded")
            show_error_dialog(self.root, "Error", "No videos loaded.")
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
            show_error_dialog(self.root, "Error", str(exc))
            return

        if from_date > to_date:
            show_error_dialog(self.root, "Error", "Từ ngày không được sau Đến ngày.")
            return

        self._append_log("[INFO] Selecting videos by upload date...")
        matched_orders: set[int] = set()
        for video in self.videos:
            if not self._video_allowed_by_short_video_setting(video):
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
                f"[SUCCESS] Selected {len(matched_orders)} videos from {from_date.isoformat()} to {to_date.isoformat()}."
            )
            context.close(True)
        else:
            self._append_log("[WARNING] No videos matched the selected date range.")

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
            manual_entry_state = "disabled"
            manual_button_state = "disabled"
            bridge_entry_state = "disabled"
            bridge_button_state = "disabled"
        else:
            source_state = "readonly"
            manual_entry_state = "readonly" if source == COOKIE_SOURCE_FILE else "disabled"
            manual_button_state = "normal" if source == COOKIE_SOURCE_FILE else "disabled"
            bridge_entry_state = "readonly" if source == COOKIE_SOURCE_BRIDGE else "disabled"
            bridge_button_state = "normal" if source == COOKIE_SOURCE_BRIDGE else "disabled"

        self.cookies_entry.configure(state=manual_entry_state)
        self.cookies_button.configure(state=manual_button_state)
        self.cookie_source_box.configure(state=source_state)
        self.bridge_cookie_entry.configure(state=bridge_entry_state)
        self.bridge_cookie_button.configure(state=bridge_button_state)
        self._update_cookie_row_visibility(source)
        self._refresh_cookie_status()

    def _update_cookie_row_visibility(self, source: str) -> None:
        if source == COOKIE_SOURCE_BRIDGE:
            self.cookies_path_label.grid_remove()
            self.cookies_entry.grid_remove()
            self.cookies_button.grid_remove()
            self.bridge_cookie_path_label.grid()
            self.bridge_cookie_entry.grid()
            self.bridge_cookie_button.grid()
            return

        self.cookies_path_label.grid()
        self.cookies_entry.grid()
        self.cookies_button.grid()
        self.bridge_cookie_path_label.grid_remove()
        self.bridge_cookie_entry.grid_remove()
        self.bridge_cookie_button.grid_remove()

    def start_download(self) -> None:
        if self.downloading:
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
            show_error_dialog(self.root, "Error", friendly)
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
        )
        save_cookie_source(options.cookie_source)
        save_bridge_cookie_path(options.bridge_cookie_path)

        try:
            validate_download_environment(options)
        except DownloadError as exc:
            friendly = self._friendly_general_message(str(exc))
            self._append_log(friendly)
            show_error_dialog(self.root, "Error", friendly)
            return

        self.downloading = True
        self.download_stop_requested = False
        self.exit_after_download_stop = False
        self.close_requested = False
        self.cancel_download = False
        self.download_controller = DownloadController()
        self._clear_progress_queue()
        self._reset_progress_sticky()
        self.progress_current_var.set("Downloading: Ready")
        self.progress_detail_var.set("Processing: -")
        self._set_download_controls_locked(True)
        worker = threading.Thread(
            target=self._download_worker,
            args=(selected, options, self.download_controller),
            daemon=True,
        )
        worker.start()

    def stop_download(self) -> None:
        if not self.downloading:
            return
        self._request_download_stop(
            exit_after=False,
            log_message="[WARNING] Người dùng đã dừng tải.",
        )

    def _request_download_stop(self, exit_after: bool, log_message: str) -> None:
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
            self._enqueue_progress_event(ProgressEvent(kind="stop_requested"))
            self._append_log("[INFO] Đang dừng tiến trình tải...")

        self._update_stop_button_state()
        if self.download_controller is not None:
            self.download_controller.request_cancel()

    def _download_worker(self, selected, options: DownloadOptions, controller: DownloadController) -> None:
        try:
            download_items(
                selected,
                options,
                self._thread_log,
                lambda video: self.events.put(("status_update", video.display_order, video.status)),
                cancel_controller=controller,
                progress_callback=self._enqueue_progress_event,
            )
            self.events.put(("download_done",))
        except DownloadError as exc:
            self._enqueue_progress_event(
                ProgressEvent(kind="error", phase="Error", message=self._friendly_general_message(str(exc)))
            )
            self.events.put(("download_error", self._friendly_general_message(str(exc))))
        except Exception as exc:
            self._enqueue_progress_event(
                ProgressEvent(kind="error", phase="Error", message=self._friendly_general_message(str(exc)))
            )
            self.events.put(("download_error", self._friendly_general_message(str(exc))))

    def apply_filter(self) -> None:
        self._destroy_status_editor()
        self._prune_selected_orders()
        selected_row = self.tree.focus()
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.visible_orders = []
        for video in self.videos:
            if not self._video_allowed_by_short_video_setting(video):
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
        )
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
        show_copy_text_dialog(self.root, "Copy video title", title)

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

    def _video_allowed_by_short_video_setting(self, video) -> bool:
        return self.show_short_videos_var.get() or not is_short_video(
            video,
            self._short_video_threshold_seconds(),
        )

    def _short_video_count(self, videos: list, threshold_minutes: int | None = None) -> int:
        threshold_seconds = (threshold_minutes or self._short_video_threshold_minutes()) * 60
        return sum(1 for video in videos if is_short_video(video, threshold_seconds))

    def _short_video_threshold_minutes(self) -> int:
        try:
            value = int(self.short_video_threshold_var.get())
        except ValueError:
            value = int(DEFAULT_SHORT_VIDEO_THRESHOLD_MINUTES)
        if str(value) not in SHORT_VIDEO_THRESHOLD_MINUTES:
            value = int(DEFAULT_SHORT_VIDEO_THRESHOLD_MINUTES)
            self.short_video_threshold_var.set(str(value))
        return value

    def _short_video_threshold_seconds(self) -> int:
        return self._short_video_threshold_minutes() * 60

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
            show_error_dialog(self.root, "Error", f"Could not save manual status:\n{exc}")
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
                show_error_dialog(self.root, "Error", f"Could not save manual status:\n{exc}")
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
                show_error_dialog(self.root, "Error", f"Could not clear manual status:\n{exc}")
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
        )
        self.more_button.configure(state="normal" if can_load_more else "disabled")

    def _update_stop_button_state(self) -> None:
        if hasattr(self, "stop_button"):
            enabled = self.downloading and not self.download_stop_requested
            self.stop_button.configure(state="normal" if enabled else "disabled")

    def _set_download_controls_locked(self, locked: bool) -> None:
        normal_state = "disabled" if locked else "normal"
        readonly_state = "disabled" if locked else "readonly"
        self.api_key_entry.configure(state=normal_state)
        self.channel_entry.configure(state=normal_state)
        self.fetch_button.configure(state=normal_state if not self.fetching else "disabled")
        self.select_by_date_button.configure(state=normal_state)
        self.choose_folder_button.configure(state=normal_state)
        self.cookies_check.configure(state=normal_state)
        self.mode_box.configure(state=readonly_state)
        self.speed_limit_entry.configure(state=normal_state)
        self.filter_box.configure(state=readonly_state)
        self.short_videos_check.configure(state=normal_state)
        self.threshold_box.configure(state=readonly_state)
        self.download_button.configure(state="disabled" if locked else "normal")
        self._update_cookies_state()
        self._update_more_button_state()
        self._update_stop_button_state()
        self._update_download_button_text()

    def _finish_download_ui(self) -> None:
        should_exit = self.exit_after_download_stop
        self.downloading = False
        self.download_controller = None
        self.download_stop_requested = False
        self.cancel_download = False
        if should_exit:
            self.close_requested = True
            self.root.after(0, self.root.destroy)
            return

        self.exit_after_download_stop = False
        self.close_requested = False
        self._set_download_controls_locked(False)

    def _on_close(self) -> None:
        if self.close_requested:
            return
        if not self.downloading:
            self.root.destroy()
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
        if self.close_requested:
            return
        self.close_requested = True
        self.cancel_download = True
        self.exit_after_download_stop = True
        self.download_stop_requested = True
        self._append_log("[WARNING] Người dùng chọn thoát ứng dụng khi đang tải.")
        self._update_stop_button_state()
        if self.download_controller is not None:
            self.download_controller.request_cancel()
        self.root.after(0, self.root.destroy)

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
        latest = None
        try:
            while True:
                latest = self.progress_queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            display_event = self._merge_progress_event_for_display(latest)
            current_line, detail_line = format_progress_event_lines(display_event)
            self.progress_current_var.set(current_line)
            self.progress_detail_var.set(detail_line)
        self.root.after(300, self._poll_progress_queue)

    def _reset_progress_sticky(self) -> None:
        self._progress_display_key = None
        self._progress_sticky_percent = None
        self._progress_sticky_speed = None
        self._progress_sticky_fragment = None

    def _merge_progress_event_for_display(self, event: ProgressEvent) -> ProgressEvent:
        if event.kind in {"batch_complete", "stop_requested", "error"}:
            self._reset_progress_sticky()
            return event

        key = (event.video_index, event.video_total, event.phase, event.title)
        if key != self._progress_display_key:
            self._reset_progress_sticky()
            self._progress_display_key = key

        if event.percent:
            self._progress_sticky_percent = event.percent
        if event.speed:
            self._progress_sticky_speed = event.speed
        if event.fragment:
            self._progress_sticky_fragment = event.fragment

        return ProgressEvent(
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
        )

    def _append_log(self, message: str) -> None:
        safe_message = sanitize_log_text(message)
        tag = self._log_tag_for(safe_message)
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", safe_message + "\n", tag)
        else:
            self.log_text.insert("end", safe_message + "\n")
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

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _handle_event(self, event) -> None:
        kind = event[0]
        if kind == "log":
            self._append_log(event[1])
        elif kind == "fetch_done":
            self.channel_info = event[1]
            self.videos = event[2]
            self.next_page_token = event[3]
            self.fetching = False
            self.fetch_button.configure(state="normal")
            self.apply_filter()
            self._update_more_button_state()
            if not self.next_page_token:
                self._append_log("[INFO] No more videos.")
        elif kind == "fetch_error":
            self._append_log(event[1])
            self.next_page_token = ""
            self.fetching = False
            self.fetch_button.configure(state="normal")
            self._update_more_button_state()
        elif kind == "load_more_done":
            more_videos = event[1]
            self.next_page_token = event[2]
            self.videos.extend(more_videos)
            self.loading_more = False
            if self.channel_info:
                apply_statuses(
                    self.videos,
                    self.save_folder_var.get().strip(),
                    self.channel_info.channel_name,
                    self.channel_info.channel_id,
                    download_mode=self.download_mode_var.get(),
                    warning_callback=self._append_log,
                )
            self.apply_filter()
            self._update_more_button_state()
        elif kind == "load_more_error":
            self._append_log(event[1])
            self.loading_more = False
            self._update_more_button_state()
        elif kind == "status_update":
            order, status = event[1], event[2]
            for video in self.videos:
                if video.display_order == order:
                    video.status = status
                    break
            self.apply_filter()
        elif kind == "download_done":
            self._finish_download_ui()
        elif kind == "download_error":
            self._append_log(event[1])
            self._finish_download_ui()


def main() -> None:
    root = tk.Tk()
    YouTubeDownloaderWindow(root)
    root.mainloop()

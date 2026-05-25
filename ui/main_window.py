import queue
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, ttk

from core.download_modes import DOWNLOAD_MODES, MODE_VIDEO_THUMB
from core.downloader import (
    DownloadController,
    DownloadError,
    DownloadOptions,
    validate_download_environment,
    validate_speed_limit,
    download_items,
)
from core.app_settings import load_last_api_key, save_last_api_key
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


class YouTubeDownloaderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube Downloaderbs")
        self.root.geometry("1100x760")
        self.root.minsize(920, 620)

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

        self._build_ui()
        self.search_var.trace_add("write", lambda *_args: self._on_search_text_changed())
        self._update_cookies_state()
        self._update_download_button_text()
        self._update_more_button_state()
        self._update_stop_button_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_events)
        self.root.after(300, self._poll_progress_queue)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(3, weight=1)
        main.rowconfigure(9, weight=1)

        ttk.Label(main, text="YouTube API Key").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.api_key_entry = ttk.Entry(main, textvariable=self.api_key_var)
        self.api_key_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(main, text="Channel URL / Channel ID / Handle").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.channel_entry = ttk.Entry(main, textvariable=self.channel_var)
        self.channel_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.fetch_button = ttk.Button(main, text="Lấy danh sách Video", command=self.start_fetch)
        self.fetch_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)

        filter_frame = ttk.Frame(main)
        filter_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        filter_frame.columnconfigure(3, weight=1)
        ttk.Label(filter_frame, text="Filter").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.filter_box = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=(FILTER_ALL, FILTER_NOT_DOWNLOADED),
            state="readonly",
            width=32,
        )
        self.filter_box.grid(row=0, column=1, sticky="w")
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_filter())
        ttk.Label(filter_frame, text="Tìm kiếm").grid(row=0, column=2, sticky="w", padx=(18, 8))
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=38)
        search_entry.grid(row=0, column=3, sticky="ew")
        search_entry.bind("<Return>", lambda _event: self._find_next_match())
        search_entry.bind("<Shift-Return>", lambda _event: self._find_previous_match())
        ttk.Label(filter_frame, textvariable=self.search_status_var, width=16).grid(
            row=0, column=4, sticky="w", padx=(8, 0)
        )
        self.short_videos_check = ttk.Checkbutton(
            filter_frame,
            text="Hiển thị video ngắn",
            variable=self.show_short_videos_var,
            command=self._on_short_video_filter_changed,
        )
        self.short_videos_check.grid(row=0, column=5, sticky="w", padx=(12, 0))
        ttk.Label(filter_frame, text="Ẩn video dưới:").grid(row=0, column=6, sticky="w", padx=(12, 4))
        self.threshold_box = ttk.Combobox(
            filter_frame,
            textvariable=self.short_video_threshold_var,
            values=SHORT_VIDEO_THRESHOLD_MINUTES,
            state="readonly",
            width=4,
        )
        self.threshold_box.grid(row=0, column=7, sticky="w")
        self.threshold_box.bind("<<ComboboxSelected>>", lambda _event: self._on_short_video_filter_changed())
        ttk.Label(filter_frame, text="phút").grid(row=0, column=8, sticky="w", padx=(4, 0))

        table_frame = ttk.Frame(main)
        table_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(0, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("selected", "title", "duration", "published", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("selected", text="[ ]", anchor="center")
        self.tree.heading("title", text="Video title")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("published", text="Upload date")
        self.tree.heading("status", text="Status")
        self.tree.column("selected", width=42, minwidth=35, stretch=False, anchor="center")
        self.tree.column("title", width=560, minwidth=240, stretch=True)
        self.tree.column("duration", width=110, minwidth=90, stretch=False, anchor="center")
        self.tree.column("published", width=130, minwidth=110, stretch=False, anchor="center")
        self.tree.column("status", width=150, minwidth=130, stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<space>", self._on_tree_space)
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
        table_actions.grid(row=1, column=0, sticky="e", pady=(6, 0))
        self.more_button = ttk.Button(table_actions, text="Xem thêm video", command=self.start_load_more)
        self.more_button.grid(row=0, column=0, sticky="e")
        self.select_by_date_button = ttk.Button(
            table_actions,
            text="Chọn video theo ngày",
            command=self.open_select_by_date_dialog,
        )
        self.select_by_date_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        ttk.Label(main, text="Save folder").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(main, textvariable=self.save_folder_var, state="readonly").grid(
            row=4, column=1, sticky="ew", pady=4
        )
        self.choose_folder_button = ttk.Button(main, text="Chọn thư mục", command=self.choose_save_folder)
        self.choose_folder_button.grid(row=4, column=2, sticky="ew", padx=(8, 0), pady=4)

        self.cookies_check = ttk.Checkbutton(
            main,
            text="Sử dụng Cookies",
            variable=self.cookies_enabled_var,
            command=self._update_cookies_state,
        )
        self.cookies_check.grid(row=5, column=0, sticky="w", pady=4)
        self.cookies_entry = ttk.Entry(main, textvariable=self.cookies_path_var, state="disabled")
        self.cookies_entry.grid(row=5, column=1, sticky="ew", pady=4)
        self.cookies_button = ttk.Button(main, text="Chọn cookies.txt", command=self.choose_cookies_file)
        self.cookies_button.grid(row=5, column=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(main, text="Kiểu tải").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=4)
        self.mode_box = ttk.Combobox(
            main,
            textvariable=self.download_mode_var,
            values=DOWNLOAD_MODES,
            state="readonly",
            width=30,
        )
        self.mode_box.grid(row=6, column=1, sticky="w", pady=4)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self._on_download_mode_changed())

        ttk.Label(main, text="Download limit").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=4)
        speed_frame = ttk.Frame(main)
        speed_frame.grid(row=7, column=1, sticky="w", pady=4)
        self.speed_limit_entry = ttk.Entry(speed_frame, textvariable=self.speed_limit_var, width=18)
        self.speed_limit_entry.grid(row=0, column=0, sticky="w")
        ttk.Label(speed_frame, text="MB/s").grid(row=0, column=1, sticky="w", padx=(6, 0))

        self.download_button = ttk.Button(main, command=self.start_download)
        self.download_button.grid(row=7, column=2, sticky="ew", padx=(8, 0), pady=4)
        self.stop_button = ttk.Button(main, text="Dừng tải", command=self.stop_download)
        self.stop_button.grid(row=8, column=2, sticky="ew", padx=(8, 0), pady=(4, 8))

        progress_frame = ttk.Frame(main)
        progress_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        progress_frame.columnconfigure(0, weight=1)
        ttk.Label(progress_frame, textvariable=self.progress_current_var, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(progress_frame, textvariable=self.progress_detail_var, anchor="w").grid(
            row=1, column=0, sticky="ew"
        )
        log_frame = ttk.Frame(main)
        log_frame.grid(row=9, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled")
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
            title="Choose cookies.txt",
            filetypes=(("Cookies file", "cookies.txt"), ("Text files", "*.txt"), ("All files", "*.*")),
        )
        if path:
            self.cookies_path_var.set(path)

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
        if self.downloading:
            state = "disabled"
        else:
            state = "normal" if self.cookies_enabled_var.get() else "disabled"
        self.cookies_entry.configure(state=state)
        self.cookies_button.configure(state=state)

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
        )

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
        self._progress_sticky_eta = None
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
        if event.eta:
            self._progress_sticky_eta = event.eta
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
            eta=event.eta or self._progress_sticky_eta,
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
